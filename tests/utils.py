import os
import condoor


def remove_cache_file():
    try:
        os.remove('/tmp/condoor.{}.db'.format(condoor.__version__))
    except OSError:
        pass