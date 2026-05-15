import os
import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add timezone to datetime import
content = content.replace('from datetime import datetime, timedelta', 'from datetime import datetime, timedelta, timezone')

# 2. Add format_timestamp helper
helper_code = """
SAST = timezone(timedelta(hours=2))

def format_timestamp(ts):
    if not ts:
        return ""
    if hasattr(ts, 'astimezone'):
        return ts.astimezone(SAST).strftime('%Y-%m-%d %H:%M')
    return str(ts)[:16]
"""

if "format_timestamp(" not in content:
    content = content.replace('import firebase_admin', helper_code + '\nimport firebase_admin')

# 3. Replace str(...get('created_at', '')) with format_timestamp(...get('created_at'))
content = re.sub(r"str\((.*?)\.get\('created_at',\s*''\)\)", r"format_timestamp(\1.get('created_at'))", content)

# 4. Replace str(...get('timestamp', '')) with format_timestamp(...)
content = re.sub(r"str\((.*?)\.get\('timestamp',\s*''\)\)", r"format_timestamp(\1.get('timestamp'))", content)

# 5. Fix CSV / PDF usages that just call str(t['created_at'])
content = re.sub(r"str\(t\['created_at'\]\)\[:16\]", r"format_timestamp(t.get('created_at'))", content)
content = re.sub(r"str\(t\['created_at'\]\)\[:10\]", r"format_timestamp(t.get('created_at'))[:10]", content)

# 6. CSV row strings
content = content.replace('{t["created_at"]}', '{format_timestamp(t.get("created_at"))}')

# 7. CSV t.get('created_at') -> we only want to replace it if it's NOT inside format_timestamp
# Let's just use regex with negative lookbehind
content = re.sub(r"(?<!format_timestamp\()t\.get\('created_at'\)", r"format_timestamp(t.get('created_at'))", content)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Formatting applied.")
