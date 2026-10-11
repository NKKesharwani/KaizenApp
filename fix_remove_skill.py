with open('E:/Personal_Projects/Kaizen/src/kaizen/bot/bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '@self.tree.command(name="remove-skill", description="Remove your active skill after confirmation.")'
new = '@self.tree.command(name="remove-skill", description="Remove your active skill after confirmation.", override=True)'

if old in content:
    content = content.replace(old, new)
    with open('E:/Personal_Projects/Kaizen/src/kaizen/bot/bot.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Replacement successful')
else:
    print('String not found')
"