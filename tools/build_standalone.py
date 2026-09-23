"""
tools/build_standalone.py — write a single self-contained HTML file of the current tracker
(page, styles, scripts, map and this week's data inlined), for emailing or offline review.

usage:  python tools/build_standalone.py [output.html]
"""
import json, os, re, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "measles_risk_tracker_standalone.html")
rd = lambda p: open(os.path.join(HERE, p), encoding="utf-8").read()
html = rd("index.html")
site = rd("data/latest/site.json"); topo = rd("site/counties-albers-10m.json")
shim = ("<script>window.MRT_STANDALONE=true;(function(){var D={'data/latest/site.json':%s,'site/counties-albers-10m.json':%s};"
        "var j=d3.json;d3.json=function(u){return u in D?Promise.resolve(D[u]):j.apply(this,arguments);};})();</script>") % (site, topo)
html = re.sub(r'<link rel="stylesheet" href="site/style.css(\?v=\w+)?">', lambda m: "<style>\n" + rd("site/style.css") + "\n</style>", html)
html = html.replace('<script src="site/vendor/d3.v7.min.js"></script>', "<script>" + rd("site/vendor/d3.v7.min.js") + "</script>")
html = html.replace('<script src="site/vendor/topojson-client.min.js"></script>', "<script>" + rd("site/vendor/topojson-client.min.js") + "</script>" + shim)
html = re.sub(r'<script src="site/app.js(\?v=\w+)?"></script>', lambda m: "<script>" + rd("site/app.js").replace("</script", "<\\/script") + "</script>", html)
open(out, "w", encoding="utf-8").write(html)
print(f"wrote {out} ({os.path.getsize(out)/1e6:.1f} MB)")
