"""Bounded Windows sharing/AV-lock retry; never falls back to non-atomic writes."""
import os,time

def replace_file(source,target,replace=None):
    replace=replace or os.replace
    for attempt in range(4):
        try:return replace(source,target)
        except PermissionError:
            if attempt==3:raise
            time.sleep(.02*(2**attempt))
