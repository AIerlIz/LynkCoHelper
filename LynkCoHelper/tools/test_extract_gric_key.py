import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import extract_gric_key


class DumpRegionTests(unittest.TestCase):
    def test_root_adbd_reads_heap_directly_without_su(self):
        data = b"\x00\r" * 2048 + b"excess"
        with patch.object(extract_gric_key, "adb", return_value=data) as adb:
            result = extract_gric_key.dump_region(None, "42", 0x2000, 0x3000)

        self.assertEqual(result, data[:4096])
        adb.assert_called_once_with(
            "exec-out",
            "dd if=/proc/42/mem bs=4096 skip=2 count=1",
            serial=None,
            binary=True,
            timeout=1800,
        )


class Hex32ReTests(unittest.TestCase):
    """密钥形态正则的边界（33 位/31 位/紧邻 hex 字符/连字符均不误报）。"""

    def test_exact_32_hex_matches(self):
        m = extract_gric_key.HEX32_RE.search(b"xx" + b"a" * 32 + b"zz")
        self.assertEqual(m.group(0), b"a" * 32)

    def test_rejects_oversized_or_undersized(self):
        self.assertIsNone(extract_gric_key.HEX32_RE.search(b"0" * 33))
        self.assertIsNone(extract_gric_key.HEX32_RE.search(b"a" * 31))

    def test_rejects_adjacent_hex_or_dash(self):
        self.assertIsNone(extract_gric_key.HEX32_RE.search(b"-" + b"a" * 32))
        self.assertIsNone(extract_gric_key.HEX32_RE.search(b"a" * 32 + b"f"))


class CfgAnchorScanTests(unittest.TestCase):
    """配置对象锚点扫描：密钥与设备字段并列时应落入锚点窗口。"""

    def test_key_in_anchor_window_is_collected(self):
        key = b"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        blob = (b"/data/user/0/com.lynkco.customer/files/.emitter\x00"
                + key + b"\x00google\x00sdk_gphone64_arm64\x00"
                b"Android 13 (API 33)")
        found = extract_gric_key.collect_cfg_anchor_candidates([blob])
        self.assertIn(key, found)

    def test_key_far_from_anchor_is_not_collected(self):
        key = b"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        blob = key + b"\x00" * 8192 + b"sdk_gphone64_arm64"
        found = extract_gric_key.collect_cfg_anchor_candidates([blob])
        self.assertNotIn(key, found)


def _make_synthetic_har(key: bytes, path: str):
    """构造自包含的验证 HAR：1 个带 x-signature 的 POST 请求。"""
    headers = {
        "accept-language": "zh_CN",
        "authorization": "fake.jwt.token",
        "x-app-id": "GEELYCNCH001M0001",
        "x-timestamp": "1700000000000",
    }
    body = '{"hbType":3}'
    method, url = "POST", "https://gric-hf-api.geely.com/ms-app-online-center/api/v2.0/app/hb"
    sts_lines = [f"{k}:{v}" for k, v in
                 ((n, headers[n]) for n in ("accept-language", "authorization"))]
    sts_lines += [f"x-{k}:{v}" for k, v in
                  sorted((n[2:], val) for n, val in headers.items() if n.startswith("x-"))]
    sts_lines.append(base64.b64encode(hashlib.md5(body.encode()).digest()).decode())
    sts_lines.append(method)
    sts_lines.append("/ms-app-online-center/api/v2.0/app/hb")
    sts = "\n".join(sts_lines)
    sig = base64.b64encode(hmac.new(key, sts.encode(), hashlib.sha256).digest()).decode()

    har = {"log": {"entries": [{"request": {
        "method": method,
        "url": url,
        "headers": [{"name": n, "value": v} for n, v in headers.items()]
                    + [{"name": "x-signature", "value": sig}],
        "postData": {"text": body},
    }}]}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(har, f)


class CmdScanEndToEndTests(unittest.TestCase):
    """cmd_scan 全链路离线模拟：未登录堆（无 STS 现场）-> 候选落盘
    -> HAR 复算 -> 配置对象锚点层级命中。不依赖任何本地敏感文件。"""

    def test_unlogged_heap_key_found_via_cfg_anchor_tier(self):
        key = b"e70024ed" + b"0" * 24          # 32-hex 测试密钥
        blob = (b"\x00" * 512
                + b"/data/user/0/com.lynkco.customer/files/.emitter\x00"
                + key + b"\x00google\x00sdk_gphone64_arm64\x00Android 13 (API 33)\x00"
                + b"0123456789abcdef0123456789abcdef" + b"\x00"   # 干扰候选
                + b"\x00" * 512)
        har_path = os.path.join(tempfile.mkdtemp(), "synthetic.har")
        _make_synthetic_har(key, har_path)
        cand_path = os.path.join(tempfile.mkdtemp(), "candidates.json")

        args = extract_gric_key.argparse.Namespace(
            serial=None, pid=None, max_mb=1200, har=har_path,
            candidates=cand_path, write=False, mask=True)
        with patch.object(extract_gric_key, "get_pid", return_value="123"), \
             patch.object(extract_gric_key, "find_heap_regions",
                          return_value=[(0, len(blob), "fake")]), \
             patch.object(extract_gric_key, "dump_region", return_value=blob):
            extract_gric_key.cmd_scan(args)   # 命中时正常 return，不 sys.exit

        cand = json.load(open(cand_path))
        self.assertEqual(cand["stsHits"], 0)
        anchor_tier = next(t for t in cand["tiers"] if "锚点" in t["name"])
        self.assertEqual(anchor_tier["count"], 2)   # 真密钥 + 1 个干扰
        self.assertIn(key.decode(), anchor_tier["candidates"])


if __name__ == "__main__":
    unittest.main()
