def is_localdb(dsn):
    import urllib
    localdb    = r'(localdb)\mssqllocaldb'
    parsed_url = urllib.parse.urlsplit(dsn)

    if   parsed_url.scheme == 'mssql':
        return parsed_url.hostname == localdb

    elif not parsed_url.scheme:
        return localdb in parsed_url.path.lower()

    else:
        return False

def is_pyinstaller():
    # https://pyinstaller.readthedocs.io/en/stable/runtime-information.html
    import sys
    return getattr(sys, 'frozen', False)
