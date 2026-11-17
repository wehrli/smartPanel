"""Raw sensor reads used by `collect/system.py`.

`system.py` (CPU / memory / swap / disk / network / temperature / battery, psutil)
and `gpu.py` (NVIDIA via pynvml, AMD via sysfs) are plain functions returning
`{METRIC: value}`; they never raise and report an unreadable value as `None`.
The v1 registry and its Claude / sessions sensors went with the GTK 3 indicator:
spend now comes from `services/spend.py`, sessions from `services/sessions.py`.
"""
