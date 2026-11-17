"""Raw sensor reads used by `collect/system.py`.

`system.py` (CPU / memory / swap / disk / network / temperature / battery, psutil)
and `gpu.py` (NVIDIA via pynvml, AMD via sysfs) are plain functions returning
`{METRIC: value}`; they never raise and report an unreadable value as `None`.
Spend comes from `services/spend.py` and sessions from `services/sessions.py`,
not from sensors here.
"""
