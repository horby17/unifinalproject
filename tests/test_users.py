import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("MPLCONFIGDIR", "/tmp/unifinalproject-test-matplotlib")
import main
from sqlalchemy import create_engine
from werkzeug.security import check_password_hash


class UserSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, 'app.db')
        engine = create_engine(f'sqlite:///{self.path}')
        self.addCleanup(engine.dispose)
        for name, value in [('DB_PATH', self.path), ('ENGINE', engine)]:
            context = patch.object(main, name, value)
            context.start()
            self.addCleanup(context.stop)
        main.init_db()
        self.client = main.app.test_client()
        self.client.post('/login', data={'username': 'admin', 'password': 'admin123'})
        self.client.get('/settings')
        with self.client.session_transaction() as session:
            self.token = session['users_csrf']

    def post(self, **data):
        return self.client.post('/settings', data={'csrf_token': self.token, **data})

    def create(self):
        self.assertEqual(self.post(action='create', username='operador', password='password123', role='user').status_code, 302)
        return main.get_user('operador')['id']

    def test_create_update_deactivate_reactivate(self):
        user_id = self.create()
        other = main.app.test_client()
        self.assertEqual(other.post('/login', data={'username': 'operador', 'password': 'password123'}).status_code, 302)
        self.assertEqual(self.post(action='update', user_id=user_id, username='operador', password='', role='user').status_code, 302)
        self.assertTrue(check_password_hash(main.get_user('operador')['password_hash'], 'password123'))
        self.assertEqual(self.post(action='toggle', user_id=user_id).status_code, 302)
        self.assertEqual(other.get('/settings').location, '/login')
        self.assertEqual(other.post('/login', data={'username': 'operador', 'password': 'password123'}).status_code, 200)
        self.assertEqual(self.post(action='toggle', user_id=user_id).status_code, 302)
        self.assertEqual(self.post(action='update', user_id=user_id, username='nuevo', password='changed123', role='admin').status_code, 302)
        self.assertIsNone(main.get_user('operador'))
        self.assertTrue(check_password_hash(main.get_user('nuevo')['password_hash'], 'changed123'))
        self.assertIn('nuevo', self.client.get('/settings').get_data(as_text=True))

    def test_permissions_csrf_duplicates_and_self_protection(self):
        self.create()
        self.assertEqual(self.post(action='create', username='operador', password='password123').status_code, 400)
        self.assertEqual(self.client.post('/settings', data={'action': 'create'}).status_code, 400)
        admin_id = main.get_user('admin')['id']
        self.assertEqual(self.post(action='toggle', user_id=admin_id).status_code, 400)
        self.assertEqual(self.post(action='update', user_id=admin_id, username='admin', role='user').status_code, 400)
        other = main.app.test_client()
        other.post('/login', data={'username': 'operador', 'password': 'password123'})
        self.assertEqual(other.post('/settings', data={'action': 'create'}).status_code, 403)
        self.assertNotIn('Usuarios del sistema', other.get('/settings').get_data(as_text=True))
        self.assertEqual(main.app.test_client().post('/register', data={'username': 'intruso', 'password': 'password123'}).location, '/login')
        self.assertIsNone(main.get_user('intruso'))

    def test_existing_database_migration(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('DROP TABLE users')
            conn.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL)')
            conn.execute("INSERT INTO users VALUES (1, 'anterior', 'hash')")
            conn.execute("INSERT INTO users VALUES (2, 'admin', 'hash')")
        main.init_db()
        main.init_db()
        self.assertEqual(main.get_user('anterior')['role'], 'user')
        self.assertEqual(main.get_user('admin')['role'], 'admin')
        self.assertEqual(main.get_user('anterior')['is_active'], 1)
