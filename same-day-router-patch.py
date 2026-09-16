from pathlib import Path
p=Path('/app/app/strategy_lab.py')
s=p.read_text(encoding='utf-8')
if 'from .same_day_sr import run_same_day_sr' not in s:
    s=s.replace('from . import strategy_lab_base as base','from . import strategy_lab_base as base\nfrom .same_day_sr import run_same_day_sr')
old='def run_lab(df,cfg):\n    if _is_opposite_recalc(cfg):'
new="def run_lab(df,cfg):\n    if cfg.get('touch_side')=='both_recalc':\n        return run_same_day_sr(df,cfg)\n    if _is_opposite_recalc(cfg):"
if old in s:
    s=s.replace(old,new)
p.write_text(s,encoding='utf-8')
print('same-day S/R editable router patched')
