import sys, urllib

defaults = {
    'port':    {
        'mssql':      1433,
        'mysql':      3306,
        'oracle':     1521,
        'postgresql': 5432
    },

    'db_user': {
        'mssql':      'sa',
        'mysql':      'root',
        'oracle':     'sys',
        'postgresql': 'postgres'
    }
}

def is_localdb(dsn):
    localdb    = r'(localdb)\mssqllocaldb'
    parsed_url = urllib.parse.urlsplit(dsn)

    if   parsed_url.scheme == 'mssql':
        return parsed_url.hostname == localdb

    elif not parsed_url.scheme:
        return localdb in parsed_url.path.lower()

    else:
        return False

# https://pyinstaller.readthedocs.io/en/stable/runtime-information.html
def is_pyinstaller():
    return getattr(sys, 'frozen', False)
