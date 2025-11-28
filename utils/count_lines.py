import pathlib

# Exclude virtual environment and cache directories
exclude_dirs = {'.venv', '__pycache__', '.git', 'venv', 'env'}

files = [f for f in pathlib.Path('.').rglob('*.py') 
         if not any(part in exclude_dirs for part in f.parts)]

total_lines = 0

for f in files:
    try:
        with open(f, encoding='utf-8', errors='ignore') as file:
            total_lines += len(file.readlines())
    except Exception as e:
        print(f"Error reading {f}: {e}")

print(f'Total Python files: {len(files)}')
print(f'Total lines: {total_lines}')

# Show breakdown by directory
breakdown = {}
for f in files:
    if len(f.parts) > 1:
        top_dir = f.parts[0]
    else:
        top_dir = 'root'
    
    with open(f, encoding='utf-8', errors='ignore') as file:
        lines = len(file.readlines())
    
    breakdown[top_dir] = breakdown.get(top_dir, 0) + lines

print('\nBreakdown by directory:')
for dir_name, lines in sorted(breakdown.items(), key=lambda x: x[1], reverse=True):
    print(f'  {dir_name}: {lines} lines')
