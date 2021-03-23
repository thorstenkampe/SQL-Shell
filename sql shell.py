#! /usr/bin/env python

import configparser, curses, os, pathlib, subprocess, sys
import click, pycompat, sshtunnel
import tunnel
# https://npyscreen.readthedocs.io/
from npyscreen import *  # NOSONAR

widget_defaults = {
    'use_two_lines':  False,
    'begin_entry_at': 17
}

dbms_defaults   = {
    'MSSQL': {
        'port':   1433,
        'user':   'sa',
        'shell':  'mssql-cli',
        'shell-windows': 'mssql-cli.bat',
        'legacy': 'sqlcmd'
    },

    'MySQL': {
        'port':   3306,
        'user':   'root',
        'shell':  'mycli',
        'legacy': 'mysql'
    },

    'Oracle': {
        'port':   1521,
        'user':   'sys',
        'shell':  'sql',
        'shell-windows': 'sql.exe',
        'legacy': 'sqlplus'
    },

    'PostgreSQL': {
        'port':   5432,
        'user':   'postgres',
        'shell':  'pgcli',
        'legacy': 'psql'
    },

    'SQLite': {
        'shell':  'litecli',
        'legacy': 'sqlite3'
    }
}

tunnel.logger.setLevel('DEBUG')
os.environ['ESCDELAY'] = '0'  # no delay on Linux for Escape key
# specify delimiter because of DSNs ("MSSQL: name = ...")
config = configparser.ConfigParser(delimiters='=')

def read_config():
    if getattr(sys, 'frozen', False):  # PyInstaller
        # https://pyinstaller.readthedocs.io/en/stable/runtime-information.html
        script_dir = sys.executable
    else:
        script_dir = __file__
    # config file in same directory as this file
    config_file = pathlib.Path(script_dir).with_name('sql shell.ini')
    config.optionxform = str  # don't lowercase DSNs
    config.read(config_file, encoding='utf-8')
    try:
        os.environ.update(config['Environment'])
    except KeyError:
        pass

class DbApp(NPSAppManaged):
    def onStart(self):
        self.registerForm('MAIN', DbParams())

class DbParams(ActionForm):
    def create(self):
        self.cycle_widgets = True

        self.name   = 'Enter parameters for database'

        self.dbtype = self.add(TitleCombo, name='* Database type:', value=0, values=['...']
                               + list(dbms_defaults), **widget_defaults)

        self.legacy_client = self.add(TitleMultiSelect, name='- Legacy client:',
                                      value=None, values=[''], max_height=2, scroll_exit=True,
                                      **widget_defaults)

        # DSNs changes in config file will not be updated in this widget
        try:
            dsns = ['...'] + [f'{index+1}. {item}' for index, item in enumerate(config['DSN'])]
        except KeyError:
            dsns = ['...']
        self.dsn    = self.add(TitleCombo, name='- DSN:', value=0, values=dsns,
                               **widget_defaults)

        self.host   = self.add(TitleText, name='- Host:', value=None, **widget_defaults)

        self.port   = self.add(TitleText, name='- Port:', value=None, **widget_defaults)

        self.db     = self.add(TitleText, name='- Database:', value=None, **widget_defaults)

        self.user   = self.add(TitleText, name='- User:', value=None, **widget_defaults)

        self.passwd = self.add(TitlePassword, name='- Password:', value=None, **widget_defaults)

    def adjust_widgets(self):
        # hide all fields except legacy client and database type if DSN selected
        for field in self.host, self.port, self.db, self.user, self.passwd:
            field.hidden = self.dsn.value

        try:  # try to set database type field to database type from DSN label
            # ['...', '1. MSSQL: name = arguments', '2. <...>'] -> '1. MSSQL: name' ->
            # ['1.', 'MSSQL:', 'name'] -> 'MSSQL:' -> 'MSSQL'
            _ = self.dsn.values[self.dsn.value].split()[1][:-1]
            # ['MSSQL', 'MySQL', ...].index('MSSQL') + 1 (offset by 1 because '...'
            # prepended in `dbtype.values`)
            self.dbtype.value = list(dbms_defaults).index(_) + 1
        except (IndexError, ValueError):
            self.dbtype.editable = True
        else:
            # if DSN label includes database type, disable editing database type
            # field
            self.dbtype.editable = False

        self.display()

    def on_cancel(self):
        if notify_yes_no('Quit application?', title='Quit application', editw=True):
            sys.exit()

    def on_ok(self):  # NOSONAR
        if not self.dbtype.value:
            notify_confirm('Database type is mandatory!', title='ERROR', editw=True)
            return

        read_config()
        dbtype      = list(dbms_defaults)[self.dbtype.value - 1]
        db_defaults = dbms_defaults[dbtype]
        shelltype   = dbtype if not self.legacy_client.value else f'{dbtype}-2'
        try:
            dsn = config['DSN'][list(config['DSN'])[self.dsn.value - 1]]
        except KeyError:
            dsn = ''
        host        = self.host.value or 'localhost'
        port        = self.port.value or db_defaults.get('port')
        user        = self.user.value or db_defaults.get('user')
        passwd      = self.passwd.value
        db          = self.db.value

        try:
            section = config[shelltype]
        except KeyError:
            section = {}

        prompt       = section.get('prompt', '')[1:-1]
        startup_file = section.get('startup_file', '')
        sqlhelp      = section.get('help')

        if self.legacy_client.value:
            defshell = db_defaults['legacy']
        else:
            if pycompat.system.is_windows:
                try:
                    defshell = db_defaults['shell-windows']
                except KeyError:
                    defshell = db_defaults['shell']
            else:
                defshell = db_defaults['shell']

        sqlshell = section.get('shell', defshell)

        # OPTIONS AND CONNECTION PARAMETERS
        # effective params are: opts[shelltype], (DSN|conn_params[dbtype]), env_vars[shelltype]
        params = {
            # `-N -C` = "encrypt, trust server certificate"  (NOSONAR)
            'MSSQL': {
                'opts':        ['-N', '-C', '--mssqlclirc', startup_file],
                'conn_params': ['-U', user, '-P', passwd, '-S', '{host},{port}', '-d', db]
            },

            'MSSQL-2': {
                'opts':        ['-N', '-C'],
                'env_vars':    {'SQLCMDINI': startup_file}
            },

            'MySQL': {
                'opts':        ['--myclirc', startup_file],
                'conn_params': ['-u', user, f'-p{passwd}', '-h', '{host}', '-P', '{port}', '-D', db]
            },

            'MySQL-2': {
                'opts':        [f'--defaults-file={startup_file}', '--protocol=TCP']
            },

            'Oracle': {
                'opts':        ['-logon'],
                'conn_params': [f'{user}/{passwd}@//{{host}}:{{port}}/{db}'],
                'env_vars':    {'SQLPATH': startup_file}
            },

            'Oracle-2': {
                'opts':        ['-l'],
                'env_vars':    {'SQLPATH': ''}
            },

            'PostgreSQL': {
                'opts':        ['--pgclirc', startup_file, '--prompt', prompt],
                'conn_params': [f'postgres://{user}:{passwd}@{{host}}:{{port}}/{db}']
            },

            'PostgreSQL-2': {
                'env_vars':    {'PSQLRC': startup_file}
            },

            'SQLite': {
                'opts':        ['--liteclirc', startup_file, '--prompt', prompt],
                'conn_params': [db]
            },

            'SQLite-2': {
                'opts':        ['-init', startup_file]
            }
        }

        opts        = params[shelltype].get('opts', [])
        conn_params = params[dbtype]['conn_params']
        env_vars    = params[shelltype].get('env_vars', {})

        # SPECIAL CASES FOR RDBMS
        if   dbtype == 'MSSQL':
            # named pipe connection to LocalDB
            localdb = r'(localdb)\mssqllocaldb'
            if localdb in dsn.lower() or host.lower() == localdb:
                opts.remove('-N')          # `-N` = "encrypt"  (NOSONAR)
                conn_params[5] = '{host}'  # host,port -> host

        elif dbtype == 'MySQL':
            if not passwd:
                conn_params.remove('-p')

            if not startup_file:
                if shelltype == 'MySQL':
                    opts = []
                else:
                    opts.remove('--defaults-file=')

        elif dbtype == 'Oracle':
            if not db:
                # remove trailing "/" because SQLcl can't handle `user@host/` connection
                # strings
                conn_params[0] = conn_params[0][:-1]

            if user == 'sys':
                conn_params += ['as', 'sysdba']

        elif dbtype == 'PostgreSQL':
            if not prompt and shelltype == 'PostgreSQL':
                opts = opts[:2]

        elif dbtype == 'SQLite':
            if not prompt and shelltype == 'SQLite':
                opts = opts[:2]

            if db:
                # replace "\" with "/" for litecli prompt
                conn_params = [pathlib.Path(db).as_posix()]

            # don't start tunnel for SQLite
            host = None
            port = None
        #

        if self.dsn.value:
            # don't start tunnel for DSN connections
            host = None
            port = None

            # DSNs have precedence over manually entered connection parameters
            if dbtype == 'SQLite':
                conn_params = [dsn]
            else:
                conn_params = dsn.split()

        curses.endwin()
        if sqlhelp:
            print(sqlhelp, end = '\n\n')

        os.environ.update(env_vars)
        try:
            with tunnel.tunnel(host, port) as dbtunnel:
                host = dbtunnel.local_bind_host
                port = str(dbtunnel.local_bind_port)

                conn_params = [param.format(host=host, port=port) for param in conn_params]
                subprocess.run([sqlshell] + opts + conn_params)  # pylint: disable = subprocess-run-check

        except KeyboardInterrupt:
            pass

        except (FileNotFoundError, ValueError, sshtunnel.BaseSSHTunnelForwarderError,
                Exception) as exception:
            print(exception)

        print()
        click.pause()
        click.clear()

read_config()
click.clear()
DbApp().run()
