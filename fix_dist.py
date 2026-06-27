f = open('app/web_app.py', 'r', encoding='utf-8')
c = f.read()
f.close()

# Find and replace the line
lines = c.split('\n')
new_lines = []
for line in lines:
    if 'dist_id_to_name' in line and 'firm_nick_name' in line:
        # Replace with just firm_name
        new_line = line.replace(
            "f\"{d['firm_name']} ({d['firm_nick_name']})\"",
            "d['firm_name']"
        )
        new_lines.append(new_line)
        print('Fixed:', new_line)
    else:
        new_lines.append(line)

f = open('app/web_app.py', 'w', encoding='utf-8')
f.write('\n'.join(new_lines))
f.close()
print('Done!')
