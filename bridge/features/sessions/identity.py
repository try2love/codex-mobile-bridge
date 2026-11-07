"""Compare the recorded and live directories of an original native session."""
import os


def same_cwd(saved, runtime, local_windows):
    if not isinstance(saved, str) or not saved or not isinstance(runtime, str) or not runtime:
        return False
    if saved == runtime:
        return True
    if not local_windows:
        return False
    try:
        return os.path.samefile(saved, runtime)
    except (OSError, ValueError):
        return False
