import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
here = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(here, sys.argv[1]), encoding="utf-8"))
print("MODEL:", d.get("model"))
print("USAGE:", d.get("usage"))
print("CONTENT:", d["choices"][0]["message"]["content"][:800])
