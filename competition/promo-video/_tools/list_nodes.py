"""列出 v2rayN 的节点（只输出名字与协议，不打印任何凭据）。

用法: python -I list_nodes.py <v2rayN_dir>
"""
import json
import os
import sys

base = sys.argv[1]
cands = [
    os.path.join(base, "guiConfigs", "guiNConfig.json"),
    os.path.join(base, "guiNConfig.json"),
]
path = next((p for p in cands if os.path.isfile(p)), None)
if not path:
    print("config not found in:", base)
    raise SystemExit(1)

cfg = json.load(open(path, encoding="utf-8"))
items = cfg.get("ServerItemList") or []
print(f"config: {path}")
print(f"nodes : {len(items)}")
for i, s in enumerate(items):
    name = s.get("remarks") or s.get("Remarks") or "?"
    ctype = s.get("configType")
    addr = s.get("address", "")
    # 只显示地址的末段，避免泄露完整凭据字段
    tail = addr[-10:] if addr else ""
    print(f"{i:>2}  {str(name)[:34]:<34} type={ctype} ...{tail}")