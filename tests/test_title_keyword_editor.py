"""Run production widget methods with minimal Qt doubles (not visual QA)."""
import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]

class Signal:
    def __init__(self):self.callbacks=[]
    def connect(self,cb):self.callbacks.append(cb)
    def emit(self,*args):
        for cb in self.callbacks:cb(*args)

class Widget:
    def __init__(self,*args):
        self._text=args[0] if args and isinstance(args[0],str) else ''
        self._enabled=True;self.children=[];self._data=None;self._value=6;self._checked=False
        self.returnPressed=Signal();self.textChanged=Signal();self.clicked=Signal()
        self.itemSelectionChanged=Signal();self.calls=[]
    def setText(self,x):self._text=x;self.textChanged.emit(x)
    def text(self):return self._text
    def clear(self):self.setText('')
    def setEnabled(self,x):self._enabled=x
    def isEnabled(self):return self._enabled
    def addWidget(self,*x):self.children.append(x[0])
    def addLayout(self,x):self.children.append(x)
    def setWordWrap(self,x):pass
    def setPlaceholderText(self,x):self.placeholder=x
    def setTextFormat(self,x):pass
    def setAutoDefault(self,x):self.auto_default=x
    def click(self):
        if self._enabled:self.clicked.emit()
    def value(self):return self._value
    def currentData(self):return self._data
    def currentText(self):return self._text
    def isChecked(self):return self._checked
    def inputMethodEvent(self,event):self.calls.append('ime')
    def keyPressEvent(self,event):self.calls.append('base-key')

class Header:
    ResizeToContents=0;Stretch=1
    def setVisible(self,x):pass
    def setSectionResizeMode(self,*args):pass

class Table(Widget):
    def __init__(self,rows,cols):super().__init__();self.rows=[];self.row=-1;self.cols=cols;self.selected=False
    def setRowCount(self,n):
        self.rows=(self.rows+[[None]*self.cols for _ in range(n)])[:n]
        if self.row>=n:self.row=-1;self.selected=False
        self.itemSelectionChanged.emit()
    def setItem(self,row,col,item):self.rows[row][col]=item
    def setHorizontalHeaderLabels(self,x):self.labels=x
    def setSelectionBehavior(self,x):pass
    def setSelectionMode(self,x):pass
    def setEditTriggers(self,x):pass
    def setMinimumHeight(self,x):pass
    def horizontalHeader(self):return Header()
    def verticalHeader(self):return Header()
    def selectRow(self,row):self.row=row;self.selected=True;self.itemSelectionChanged.emit()
    def currentRow(self):return self.row
    def selectedItems(self):return self.rows[self.row] if self.selected and self.row>=0 else []
    def rowCount(self):return len(self.rows)
    def item(self,row,col):return self.rows[row][col]

qt=types.ModuleType('PySide6.QtCore');qt.Qt=types.SimpleNamespace(PlainText=0,Key_Return=1,Key_Enter=2)
widgets=types.ModuleType('PySide6.QtWidgets')
for name in ['QWidget','QVBoxLayout','QHBoxLayout','QLabel','QLineEdit','QPushButton','QTableWidgetItem']:
    setattr(widgets,name,Widget)
widgets.QTableWidget=Table;widgets.QHeaderView=Header
widgets.QAbstractItemView=types.SimpleNamespace(SelectRows=0,SingleSelection=1,NoEditTriggers=2)
pyside=types.ModuleType('PySide6');pyside.QtCore=qt;pyside.QtWidgets=widgets
with patch.dict(sys.modules,{'PySide6':pyside,'PySide6.QtCore':qt,'PySide6.QtWidgets':widgets}):
    spec=importlib.util.spec_from_file_location('_title_keyword_widget_test',ROOT/'ui/widgets/title_keyword_editor.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class WidgetTests(unittest.TestCase):
    def setUp(self):self.editor=module.TitleKeywordEditor()
    def add(self,text):self.editor.keyword_edit.setText(text);self.editor.add_button.click()
    def test_empty_initial_state(self):
        self.assertEqual(self.editor.keywords(),[])
        self.assertFalse(self.editor.delete_button.isEnabled())
    def test_add_literal_keyword(self):
        self.add('(Insturumental)')
        self.assertEqual(self.editor.keywords(),['(Insturumental)'])
        self.assertEqual(self.editor.table.item(0,1).text(),'(Insturumental)')
        self.assertEqual(self.editor.keyword_edit.text(),'')
    def test_delete_selected_reindexes(self):
        self.add('first');self.add('second');self.editor.table.selectRow(0);self.editor.delete_button.click()
        self.assertEqual(self.editor.keywords(),['second'])
        self.assertEqual(self.editor.table.item(0,0).text(),'1')
    def test_last_deletion_disables_delete(self):
        self.add('one');self.editor.delete_button.click()
        self.assertEqual(self.editor.keywords(),[]);self.assertFalse(self.editor.delete_button.isEnabled())
    def test_blank_rejected(self):
        self.add('   ');self.assertEqual(self.editor.keywords(),[]);self.assertTrue(self.editor.message_label.text())
    def test_duplicate_case_variant_not_added(self):
        self.add('(X)');self.add('(x)')
        self.assertEqual(self.editor.keywords(),['(X)'])
        self.assertEqual(self.editor.table.currentRow(),0)
    def test_50_limit_and_delete_then_add(self):
        for i in range(50):self.add(f'({i})')
        self.assertEqual(self.editor.table.rowCount(),50)
        self.assertFalse(self.editor.add_button.isEnabled())
        self.editor.keyword_edit.setText('overflow');self.editor.add_keyword()
        self.assertEqual(len(self.editor.keywords()),50)
        self.editor.delete_button.click();self.assertTrue(self.editor.add_button.isEnabled())
        self.add('replacement');self.assertEqual(len(self.editor.keywords()),50)
    def test_load_and_read_are_defensive_copies(self):
        source=['a'];self.editor.set_keywords(source);source.append('b')
        result=self.editor.keywords();result.append('c')
        self.assertEqual(self.editor.keywords(),['a'])
    def test_preview_is_filtered_and_is_not_a_saved_keyword(self):
        self.add('(x)');self.editor.preview_edit.setText('Song(x)')
        self.assertEqual(self.editor.preview_label.text(),'\u9664\u53bb\u5f8c: Song')
        self.assertEqual(self.editor.keywords(),['(x)'])
    def test_enter_adds_and_consumes_event(self):
        self.editor.keyword_edit.setText('(x)')
        event=Mock(key=Mock(return_value=qt.Qt.Key_Return))
        self.editor.keyword_edit.keyPressEvent(event)
        self.assertEqual(self.editor.keywords(),['(x)']);event.accept.assert_called_once()
        self.assertNotIn('base-key',self.editor.keyword_edit.calls)
    def test_ime_enter_does_not_add(self):
        self.editor.keyword_edit.setText('composing');self.editor.keyword_edit._composing=True
        event=Mock(key=Mock(return_value=qt.Qt.Key_Return))
        self.editor.keyword_edit.keyPressEvent(event)
        self.assertEqual(self.editor.keywords(),[])
        event.accept.assert_called_once()

class DialogBase:
    def accept(self):self.accepted=True
    def reject(self):self.rejected=True

def dialog_type():
    tree=ast.parse((ROOT/'ui/dialogs/settings_dialog.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='SettingsDialog')
    cls.bases=[ast.Name(id='DialogBase',ctx=ast.Load())]
    cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('accept','reject','_init_title_keywords_tab')]
    ns={'DialogBase':DialogBase,'QMessageBox':types.SimpleNamespace(critical=Mock())}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),str(ROOT/'ui/dialogs/settings_dialog.py'),'exec'),ns)
    return ns['SettingsDialog'],ns

class DialogTests(unittest.TestCase):
    def dialog(self,save_ok=True):
        cls,ns=dialog_type();d=cls();d.accepted=False;d.rejected=False
        # All standard fields are real interface-shaped doubles; the production
        # accept() body constructs and submits the complete configuration.
        source=ast.parse((ROOT/'ui/dialogs/settings_dialog.py').read_text())
        method=next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=='accept')
        for n in ast.walk(method):
            if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id=='self':
                if not hasattr(d,n.attr):setattr(d,n.attr,Widget())
        d.youtube_api_keys=['existing-api-key'];d.youtube_active_key_index=0
        d.youtube_api_key_store=types.SimpleNamespace(save=Mock(return_value=True))
        d.config_service=types.SimpleNamespace(save_config=Mock(return_value=save_ok))
        d.title_keyword_editor=module.TitleKeywordEditor();d.title_keyword_editor.set_keywords(['(x)','\uff08TV\u30b5\u30a4\u30ba\uff09'])
        d._restore_hotkeys=Mock();d._restore_midi_config=Mock();d._get_track_info_position_value=Mock(return_value='scroll')
        return d,ns
    def test_apply_saves_list_and_preserves_other_fields(self):
        d,ns=self.dialog();d.accept()
        saved=d.config_service.save_config.call_args.args[0]
        self.assertEqual(saved['track_title_remove_keywords'],['(x)','\uff08TV\u30b5\u30a4\u30ba\uff09'])
        self.assertEqual(saved['shazam_lane_count'],6)
        self.assertTrue(d.accepted)
    def test_cancel_does_not_save_list(self):
        d,ns=self.dialog();d.reject();d.config_service.save_config.assert_not_called()
        self.assertTrue(d.rejected)
    def test_failed_save_keeps_dialog_open(self):
        d,ns=self.dialog(False);d.accept()
        self.assertFalse(d.accepted);ns['QMessageBox'].critical.assert_called_once()
    def test_new_tab_uses_actual_editor(self):
        cls,ns=dialog_type();d=cls();d.tabs=types.SimpleNamespace(addTab=Mock())
        with patch.dict(sys.modules,{'ui.widgets.title_keyword_editor':module}):d._init_title_keywords_tab()
        self.assertIsInstance(d.title_keyword_editor,module.TitleKeywordEditor)
        self.assertEqual(d.tabs.addTab.call_args.args[1],'\u66f2\u540d\u30ad\u30fc\u30ef\u30fc\u30c9\u9664\u53bb')

if __name__=='__main__':unittest.main()
