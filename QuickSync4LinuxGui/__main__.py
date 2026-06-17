import sys

from . import quicksync

if __name__ == '__main__':
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] in ('gui', '--gui', '-g')):
        try:
            from . import gui
            gui.run()
        except ImportError:
            print('PySide6 is not installed. Install it with: pip install PySide6')
            sys.exit(1)
    else:
        quicksync.main()