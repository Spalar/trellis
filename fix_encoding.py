content = open('scripts/security_scan.py', 'r', encoding='utf-8').read()
content = content.replace('rust_file.read_text()', 'rust_file.read_text(encoding="utf-8", errors="replace")')
open('scripts/security_scan.py', 'w', encoding='utf-8').write(content)
print('Fixed read_text encoding')
