from pathlib import Path
import json
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "desktop" / "web" / "app.js"


class WebUIRuntimeTests(unittest.TestCase):
    def test_quit_terminates_startup_and_late_poll_results(self):
        """A terminating local assistant must not restart polling or overwrite its notice."""
        script = textwrap.dedent(
            '''\
            const fs = require("fs");
            const appSource = fs.readFileSync(__APP_PATH__, "utf8");
            const tick = () => new Promise((resolve) => setImmediate(resolve));
            const ticks = async (count = 4) => {{ for (let index = 0; index < count; index += 1) await tick(); }};
            const deferred = () => {{
              let resolve, reject;
              const promise = new Promise((ok, fail) => {{ resolve = ok; reject = fail; }});
              return {{ promise, resolve, reject }};
            }};
            const response = (data) => ({{ json: async () => ({{ ok: true, data }}) }});
            const failure = (error) => ({{ json: async () => ({{ ok: false, error }}) }});
            const fixture = (running = false) => ({{
              hasIdentity: true, connected: true, configured: true, binding: null,
              candidate: null, capture: {{ stage: "idle", events: [] }},
              proxy: {{ running, paired: false, pairUrl: null, address: "127.0.0.1", port: 8080 }},
              scheduleWindows: {{ items: [] }}, runs: {{ items: [], nextCursor: null }},
            }});
            class Element {{
              constructor(id) {{
                this.id = id; this.disabled = false; this.hidden = false; this.checked = false;
                this.value = ""; this.dataset = {{}}; this.textContent = ""; this.listeners = {{}};
                const classes = new Set();
                this.classList = {{
                  add: (...items) => items.forEach((item) => classes.add(item)),
                  remove: (...items) => items.forEach((item) => classes.delete(item)),
                  toggle: (item, force) => {{ force ? classes.add(item) : classes.delete(item); }},
                  contains: (item) => classes.has(item),
                }};
              }}
              addEventListener(type, handler) {{ (this.listeners[type] ||= []).push(handler); }}
              setAttribute() {{}} removeAttribute() {{}} replaceChildren() {{}} append() {{}} prepend() {{}} remove() {{}}
              querySelector() {{ return new Element("child"); }} querySelectorAll() {{ return []; }}
              closest() {{ return this; }} focus() {{}}
            }}
            function boot(fetchImpl, autoStart = true) {{
              const elements = new Map();
              const get = (id) => {{
                if (!elements.has(id)) elements.set(id, new Element(id));
                return elements.get(id);
              }};
              const timers = [], cleared = [];
              global.document = {{
                hidden: false, activeElement: null,
                getElementById: get,
                querySelector: (selector) => selector === "main" ? get("main") : null,
                querySelectorAll: (selector) => selector === "button" ? [get("quit")] : [],
                createElement: () => new Element("created"), createTextNode: () => new Element("text"),
              }};
              global.window = {{}};
              global.location = {{ hash: "", pathname: "/", search: "" }};
              global.history = {{ replaceState() {{}} }};
              global.sessionStorage = {{ getItem: () => "fixture-token", setItem() {{}} }};
              global.AbortSignal = {{ timeout: () => ({{}}) }};
              global.setInterval = (callback) => {{ const timer = {{ callback, active: true }}; timers.push(timer); return timer; }};
              global.clearInterval = (timer) => {{ if (timer) {{ timer.active = false; cleared.push(timer); }} }};
              global.fetch = fetchImpl;
              const source = autoStart ? appSource : appSource.replace(
                "  start();\\n})();",
                "  global.__desktopTest = {{ poll, setState: (next) => {{ state = next; }} }};\\n})();",
              );
              eval(source);
              return {{ get, timers, cleared }};
            }}
            const click = (element) => element.listeners.click[0]({{ currentTarget: element }});
            (async () => {{
              const initialStatus = deferred();
              let startupStatusCalls = 0;
              const startup = boot((path) => {{
                if (path === "/api/status") return startupStatusCalls++ === 0 ? initialStatus.promise : Promise.resolve(response(fixture()));
                if (path === "/api/networks") return Promise.resolve(response([]));
                if (path === "/api/refresh") return Promise.resolve(response({{}}));
                if (path === "/api/quit") return Promise.resolve(response({{}}));
                throw new Error(`unexpected startup request ${{path}}`);
              }});
              await ticks();
              click(startup.get("quit"));
              await ticks();
              initialStatus.resolve(response(fixture()));
              await ticks(8);
              if (startup.timers.length !== 0) throw new Error("startup installed a poll timer after quit");
              if (startup.get("notice").textContent !== "助手已退出，可以关闭此页面。") throw new Error("startup overwrote quit notice");

              const running = boot((path) => {{
                if (path === "/api/status") return Promise.resolve(response(fixture()));
                if (path === "/api/networks") return Promise.resolve(response([]));
                if (path === "/api/refresh") return Promise.resolve(response({{}}));
                if (path === "/api/quit") return Promise.resolve(response({{}}));
                throw new Error(`unexpected running request ${{path}}`);
              }});
              await ticks(10);
              if (running.timers.length !== 1 || !running.timers[0].active) throw new Error("startup did not retain its poll timer");
              click(running.get("quit"));
              await ticks();
              if (running.cleared.length !== 1 || running.timers[0].active) throw new Error("quit did not clear the active poll timer");

              const pendingPoll = deferred();
              const latePoll = boot((path) => {{
                if (path === "/api/status") return pendingPoll.promise;
                if (path === "/api/quit") return Promise.resolve(response({{}}));
                throw new Error(`unexpected poll request ${{path}}`);
              }}, false);
              global.__desktopTest.setState(fixture());
              const inFlight = global.__desktopTest.poll();
              await ticks();
              click(latePoll.get("quit"));
              await ticks();
              pendingPoll.resolve(response(fixture()));
              await inFlight;
              await ticks();
              if (latePoll.get("notice").textContent !== "助手已退出，可以关闭此页面。") throw new Error("late poll overwrote quit notice");

              const retryable = boot((path) => {{
                if (path === "/api/quit") return Promise.resolve(failure("服务暂时异常"));
                throw new Error(`unexpected retry request ${{path}}`);
              }}, false);
              global.__desktopTest.setState(fixture());
              click(retryable.get("quit"));
              await ticks();
              if (retryable.get("quit").disabled) throw new Error("reachable JSON failure did not restore quit");
              if (retryable.get("quit").classList.contains("is-loading")) throw new Error("reachable JSON failure kept loading state");
              if (retryable.get("notice").textContent !== "服务暂时异常") throw new Error("reachable JSON error was not shown");

              const unreachable = boot((path) => {{
                if (path === "/api/quit") return Promise.reject(new TypeError("network down"));
                throw new Error(`unexpected network request ${{path}}`);
              }}, false);
              global.__desktopTest.setState(fixture());
              click(unreachable.get("quit"));
              await ticks();
              if (!unreachable.get("quit").disabled) throw new Error("unreachable helper left quit enabled");
              if (!unreachable.get("main").classList.contains("is-disconnected")) throw new Error("unreachable helper did not enter disconnected state");
              if (unreachable.get("notice").textContent !== "本机连接已断开，请重新双击打开助手。") throw new Error("unreachable helper did not show recovery message");
            }})().catch((error) => {{ console.error(error); process.exit(1); }});
            '''.replace("__APP_PATH__", json.dumps(str(APP))).replace("{{", "{").replace("}}", "}")
        )
        result = subprocess.run(
            ["node", "-e", script], text=True, capture_output=True, cwd=ROOT
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
