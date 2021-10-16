#! /usr/bin/env python

import configparser, curses, os, pathlib, subprocess, sys
import click, pycompat
import toolbox as tb, tunnel
# https://npyscreen.readthedocs.io/
from npyscreen import *  # NOSONAR

widget_defaults = {'use_two_lines': False, 'begin_entry_at': 17}
dbms_types      = ['MSSQL', 'MySQL', 'Oracle', 'PostgreSQL', 'SQLite']

tunnel.logger.setLevel('DEBUG')
os.environ['ESCDELAY'] = '0'  # no delay on Linux for Escape key

# Read config file
# specify delimiter because of DSNs ("MSSQL: name = ...")
config = configparser.ConfigParser(delimiters='=')

if tb.is_pyinstaller():
    # https://pyinstaller.readthedocs.io/en/stable/runtime-information.html#using-sys-executable-and-sys-argv-0
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
                               + dbms_types, **widget_defaults)

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
        hide_fields = self.host, self.port, self.user, self.passwd

        for field in hide_fields + (self.db,):
            field.hidden = False

        # hide host, port, database, user, and password field if DSN selected
        if   self.dsn.value:
            for field in hide_fields + (self.db,):
                field.hidden = True

        # hide host, port, user, and password field if SQLite selected
        elif self.dbtype.value == 5:
            for field in hide_fields:
                field.hidden = True

        try:  # try to set database type field to database type from DSN label
            # ['...', '1. MSSQL: name = arguments', '2. <...>'] -> '1. MSSQL: name' ->
            # ['1.', 'MSSQL:', 'name'] -> 'MSSQL:' -> 'MSSQL'
            _ = self.dsn.values[self.dsn.value].split()[1][:-1]
            # ['MSSQL', 'MySQL', ...].index('MSSQL') + 1 (offset by 1 because '...'
            # prepended in `dbtype.values`)
            self.dbtype.value = dbms_types.index(_) + 1
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

        if not (self.user.value or self.dsn.value or self.dbtype.value == 5):  # `5` is SQLite
            notify_confirm('User is mandatory!', title='ERROR', editw=True)
            return

        dbtype = dbms_types[self.dbtype.value - 1]
        try:
            dsn = config['DSN'][list(config['DSN'])[self.dsn.value - 1]]
        except KeyError:
            dsn = ''
        host   = self.host.value or 'localhost'
        user   = self.user.value
        passwd = self.passwd.value
        db     = self.db.value

        try:
            section = config[dbtype]
        except KeyError:
            section = {}

        prompt       = section.get('prompt', '')[1:-1]
        startup_file = section.get('startup_file', '')
        sqlhelp      = section.get('help')
        legacy       = section.get('legacy')

        # CONNECTION PARAMETERS AND OPTIONS
        opts     = []
        env_vars = {}

        if   dbtype == 'MSSQL':
            port        = self.port.value or 1433
            conn_params = ['-U', user, '-P', passwd, '-S', '{host},{port}', '-d', db]

            if legacy:
                defshell = 'sqlcmd'
                opts     = ['-N', '-C']
                env_vars = {'SQLCMDINI': startup_file}
            else:
                if pycompat.system.is_windows:
                    defshell = 'mssql-cli.bat'
                else:
                    defshell = 'mssql-cli'
                # `-N -C` = "encrypt, trust server certificate"  (NOSONAR)
                opts = ['-N', '-C', '--mssqlclirc', startup_file]

            # named pipe connection to LocalDB
            if tb.is_localdb(dsn) or tb.is_localdb(host):
                conn_params[5] = '{host}'  # host,port -> host
                opts.remove('-N')          # `-N` = "encrypt"  (NOSONAR)

        elif dbtype == 'MySQL':
            port        = self.port.value or 3306
            conn_params = ['-u', user, '-h', '{host}', '-P', '{port}', '-D', db]

            if legacy:
                defshell = 'mysql'
                opts     = ['--protocol=TCP']
                if startup_file:
                    # `--defaults-file` must be first option
                    opts = [f'--defaults-file={startup_file}'] + opts

            else:
                defshell = 'mycli'
                if startup_file:
                    opts = ['--myclirc', startup_file]

            if passwd:
                conn_params += [f'-p{passwd}']

        elif dbtype == 'Oracle':
            port        = self.port.value or 1521
            conn_params = [f'{user}/{passwd}@//{{host}}:{{port}}']

            if legacy:
                defshell = 'sqlplus'
                opts     = ['-l']
                env_vars = {'SQLPATH': ''}
            else:
                if pycompat.system.is_windows:
                    defshell = 'sql.exe'
                else:
                    defshell = 'sql'
                opts     = ['-logon']
                env_vars = {'SQLPATH': startup_file}

            if db:
                # SQLcl can't handle `user@host/` connection strings
                conn_params[0] += f'/{db}'

            if user == 'sys':
                conn_params += ['as', 'sysdba']

        elif dbtype == 'PostgreSQL':
            port        = self.port.value or 5432
            conn_params = [f'postgres://{user}:{passwd}@{{host}}:{{port}}/{db}']

            if legacy:
                defshell = 'psql'
                env_vars = {'PSQLRC': startup_file}
            else:
                defshell = 'pgcli'
                opts     = ['--pgclirc', startup_file]
                if prompt:
                    opts += ['--prompt', prompt]

        elif dbtype == 'SQLite':
            # don't start tunnel for SQLite
            host = None
            port = None

            if db:
                # replace "\" with "/" for litecli prompt
                conn_params = [pathlib.Path(db).as_posix()]
            else:
                conn_params = [db]

            if legacy:
                defshell = 'sqlite3'
                opts     = ['-init', startup_file]
            else:
                defshell = 'litecli'
                opts     = ['--liteclirc', startup_file]
                if prompt:
                    opts += ['--prompt', prompt]

        # noinspection PyUnboundLocalVariable
        sqlshell = section.get('shell', defshell)

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
            # noinspection PyUnboundLocalVariable
            with tunnel.tunnel(host, port) as dbtunnel:
                host = dbtunnel.local_bind_host
                port = str(dbtunnel.local_bind_port)

                # noinspection PyUnboundLocalVariable
                conn_params = [param.format(host=host, port=port) for param in conn_params]
                subprocess.run([sqlshell] + opts + conn_params)  # pylint: disable = subprocess-run-check

        except KeyboardInterrupt:
            pass

        except Exception as exception:
            print(exception)

        print()
        click.pause()
        click.clear()

click.clear()
DbApp().run()
