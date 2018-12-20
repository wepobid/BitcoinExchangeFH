import logging
from datetime import datetime
from time import sleep

from sqlalchemy import (
    create_engine,
    Table,
    Column,
    Integer,
    String,
    Numeric,
    MetaData)

from .handler import Handler

LOGGER = logging.getLogger(__name__)


class SqlHandler(Handler):
    """Sql handler.
    """

    def __init__(self, connection, **kwargs):
        """Constructor.
        """
        super().__init__(**kwargs)
        self._connection = connection
        self._engine = None
        self._meta_data = None
        self._queue = None
        self._tables = {}

    @property
    def queue(self):
        """Queue.
        """
        return self._queue

    def load(self, queue):
        """Load.
        """
        self._engine = create_engine(self._connection)
        self._meta_data = MetaData()
        self._queue = queue

    def create_table(self, table_name, fields, **kwargs):
        """Create table.
        """
        assert self._engine, "Engine is not initialized"

        # Check if the table exists
        if table_name in self._engine.table_names():
            if self._is_cold:
                self._engine.execute(
                    'delete table {table_name}'.format(
                        table_name=table_name))
                LOGGER.info(
                    'Table %s is deleted in cold mode',
                    table_name)
            else:
                LOGGER.info('Table %s is created', table_name)
                self._tables[table_name] = Table(
                    table_name, self._meta_data, autoload=True,
                    autoload_with=self._engine)
                return

        LOGGER.info ('Creating table %s', table_name)
        columns = []

        for field_name, field in fields.items():
            columns.append(self._create_column(
                field_name=field_name,
                field=field))

        self._tables[table_name] = Table(table_name, self._meta_data, *columns)
        self._meta_data.create_all(self._engine)
        LOGGER.info ('Created table %s', table_name)

    def insert(self, table_name, fields, **kwargs):
        """Insert.
        """
        assert self._engine, "Engine is not initialized"

        fields = [
            (k, v) for k, v in fields.items() if not v.is_auto_increment]
        fields = list(zip(*fields))

        column_names = (','.join(fields[0]))
        values = (','.join([str(f) for f in fields[1]]))

        sql_statement = (
            "insert into {table_name} ({column_names}) values "
            "({values})").format(
                table_name=table_name,
                column_names=column_names,
                values=values)

        self._queue.put(sql_statement)

        if self._is_debug:
            LOGGER.info(sql_statement)

    def rename_table(self, from_name, to_name, fields=None, keep_table=True):
        """Rename table.
        """
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        conn = self._engine.connect()
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        op.rename_table(from_name, to_name)

        if keep_table:
            assert fields is not None, (
                "Fields must be provided to create the table")
            self._meta_data.create_all(self._engine)

    def rotate_table(self, table, last_datetime):
        """Rotate table.
        """
        from_name = table.table_name
        to_name = "%s_%s" % (
            from_name, last_datetime.strftime(self._rotate_frequency))

        LOGGER.info('Rotate table from %s to %s',
                    from_name,
                    to_name)
        self.rename_table(
            from_name=from_name,
            to_name=to_name,
            fields=table.fields,
            keep_table=True)

    def run(self):
        """Run.
        """
        while True:
            while not self._queue.empty():
                element = self._queue.get()
                self._engine.execute(element)

            sleep(1)

    @staticmethod
    def _create_column(field_name, field):
        """Create column.
        """
        field_params = {}

        if field.field_type is int:
            field_type = Integer
        elif field.field_type is str:
            field_type = String(field.field_length)
        elif field.field_type is float:
            field_type = Numeric(
                precision=field.size,
                scale=field.decimal)
        elif field.field_type is datetime:
            field_type = String(26)
        else:
            raise NotImplementedError(
                'Field type {type} not implemented'.format(
                    type=field.field_type))

        if field.is_key:
            field_params['primary_key'] = True

        if field.is_auto_increment:
            field_params['autoincrement'] = True

        return Column(field_name, field_type, **field_params)