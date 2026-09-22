from pathlib import Path
import json
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'desktop' / 'web' / 'app.js'


class WebUIRuntimeTests(unittest.TestCase):
    def test_quit_blocks_duplicate_clicks_and_keeps_visible_progress(self):
        """Removing the quit guard or loading state makes this test fail."""
        script = textwrap.dedent(
            f'''\
            const fs = require("fs");
            (async () => {{
            class Element {{
              constructor(id) {{
                this.id = id; this.disabled = false; this.hidden = false;
                this.dataset = {{}}; this.textContent = ""; this.listeners = {{}};
                const classes = new Set();
                this.classList = {{
                  add: (...items) => items.forEach((item) => classes.add(item)),
                  remove: (...items) => items.forEach((item) => classes.delete(item)),
                  toggle: (item, force) => {{ force ? classes.add(item) : classes.delete(item); }},
                  contains: (item) => classes.has(item),
                }};
              }}
              addEventListener(type, handler) {{ (this.listeners[type] ||= []).push(handler); }}
              setAttribute() {{}} removeAttribute() {{}}
              replaceChildren() {{}} append() {{}} remove() {{}}
              querySelector() {{ return new Element("child"); }} querySelectorAll() {{ return []; }}
              closest() {{ return this; }} focus() {{}}
            }}
            const elements = new Map();
            const get = (id) => {{
              if (!elements.has(id)) elements.set(id, new Element(id));
              return elements.get(id);
            }};
            const quit = get("quit");
            global.document = {{
              hidden: false,
              getElementById: get,
              querySelector: () => get("main"),
              querySelectorAll: (selector) => selector === "button" ? [quit] : [],
              createElement: () => new Element("created"),
            }};
            global.window = {{}};
            global.location = {{ hash: "", pathname: "/", search: "" }};
            global.history = {{ replaceState() {{}} }};
            global.sessionStorage = {{ getItem: () => "fixture-token", setItem() {{}} }};
            global.AbortSignal = {{ timeout: () => ({{}}) }};
            global.setInterval = () => 1;
            global.clearInterval = () => {{}};
            let quitCalls = 0, resolveQuit;
            global.fetch = (path) => {{
              if (path === "/api/status") return new Promise(() => {{}});
              if (path !== "/api/quit") throw new Error(`unexpected request ${{path}}`);
              quitCalls += 1;
              return new Promise((resolve) => {{
                resolveQuit = () => resolve({{ json: async () => ({{ ok: true, data: {{}} }}) }});
              }});
            }};
            eval(fs.readFileSync({json.dumps(str(APP))}, "utf8"));
            const click = quit.listeners.click[0];
            click({{ currentTarget: quit }});
            await Promise.resolve();
            if (!quit.disabled || !quit.classList.contains("is-loading")) throw new Error("quit is not visibly loading");
            click({{ currentTarget: quit }});
            if (quitCalls !== 1) throw new Error(`quit sent ${{quitCalls}} requests`);
            resolveQuit();
            await new Promise((resolve) => setImmediate(resolve));
            if (!quit.disabled) throw new Error("quit is enabled after successful shutdown");
            if (get("notice").textContent !== "助手已退出，可以关闭此页面。") throw new Error("success notice missing");
            }})().catch((error) => {{ console.error(error); process.exit(1); }});
            '''
        )
        result = subprocess.run(
            ['node', '-e', script],
            text=True,
            capture_output=True,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == '__main__':
    unittest.main()
