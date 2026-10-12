const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function fixture() {
  const element = (dataset = {}) => ({dataset, value: '', hidden: false, attrs: {}, listeners: {},
    addEventListener(event, handler) {this.listeners[event] = handler;},
    setAttribute(key, value) {this.attrs[key] = value;},
    focus() {}, showModal() {this.open = true;}, close() {this.open = false;}, reset() {},
    fire(event) {this.listeners[event]();}
  });
  const ids = Object.fromEntries(['user-dialog', 'user-form', 'user-action', 'user-password', 'user-dialog-title', 'user-save', 'user-password-label', 'user-password-help', 'user-name', 'user-id', 'user-role', 'create-user', 'user-search', 'users-empty', 'user-count', 'users-page', 'users-prev', 'users-next'].map(id => [id, element()]));
  const rows = Array.from({length: 12}, (_, i) => element({name: `Usuario${i}`, state: i % 2 ? 'inactive' : 'active'}));
  const tabs = ['all', 'active', 'inactive'].map(status => element({status}));
  const edit = element({editUser: '3', username: 'Usuario3', role: 'admin'});
  const close = element();
  const selectors = {'[data-user-row]': rows, '[data-status]': tabs, '[data-edit-user]': [edit], '[data-close-dialog]': [close]};
  const document = {getElementById: id => ids[id], querySelectorAll: selector => selectors[selector]};
  vm.runInNewContext(fs.readFileSync('static/js/users.js', 'utf8'), {document});
  return {ids, rows, tabs, edit, close};
}

test('user tabs, search and pagination combine correctly', () => {
  const {ids, rows, tabs} = fixture();
  assert.equal(rows.filter(row => !row.hidden).length, 10);
  ids['users-next'].fire('click');
  assert.equal(rows.filter(row => !row.hidden).length, 2);
  tabs[1].fire('click');
  assert.equal(rows.filter(row => !row.hidden).length, 6);
  assert.equal(ids['users-page'].textContent, 'Página 1 de 1');
  ids['user-search'].value = 'usuario10';
  ids['user-search'].fire('input');
  assert.equal(rows.filter(row => !row.hidden).length, 1);
  tabs[2].fire('click');
  assert.equal(rows.filter(row => !row.hidden).length, 0);
  assert.equal(ids['users-empty'].hidden, false);
});

test('editing preserves role and allows an empty password; creating requires one', () => {
  const {ids, edit, close} = fixture();
  edit.fire('click');
  assert.equal(ids['user-dialog'].open, true);
  assert.equal(ids['user-id'].value, '3');
  assert.equal(ids['user-role'].value, 'admin');
  assert.equal(ids['user-password'].required, false);
  close.fire('click');
  assert.equal(ids['user-dialog'].open, false);
  ids['create-user'].fire('click');
  assert.equal(ids['user-action'].value, 'create');
  assert.equal(ids['user-name'].value, '');
  assert.equal(ids['user-password'].required, true);
});
