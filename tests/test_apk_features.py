import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import apk_features as f


class ResourceTests(unittest.TestCase):
    def test_linked_text_only_and_variants_deduplicated(self):
        table = '''Package name=test.app id=7f
    resource 0x7f01 layout/settings
      () (file) res/a.xml type=XML
      (land) (file) res/b.xml type=XML
    resource 0x7f02 string/settings
      () "Settings"
      (ko) "설정"
    resource 0x7f03 string/unrelated
      () "구매"
    resource 0x7f04 id/config
    resource 0x7f05 layout/abc_toolbar
      () (file) res/lib.xml type=XML'''
        tree = '''E: LinearLayout (line=2)
    E: Button (line=5)
      A: http://schemas.android.com/apk/res/android:id(0x010100d0)=@0x7f04
      A: http://schemas.android.com/apk/res/android:text(0x0101014f)=@0x7f02
    E: FrameLayout (line=20)
      A: http://schemas.android.com/apk/res/android:visibility(0x010100dc)=0x00000008
        E: TextView (line=21)
          A: http://schemas.android.com/apk/res/android:text(0x0101014f)="숨김"
    E: WebView (line=23)'''
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.apk'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('resources.arsc','fixture')
            with patch.object(f,'aapt',return_value='fixture'),patch.object(f,'dump',side_effect=[table,tree,tree]):
                items,pending,excluded=f.extract(path,f.digest(path),{'package_name':'test.app',
                    'components':[{'type':'activity','name':'.SettingsActivity'}]},lambda _:None,lambda:False)
        self.assertEqual([(x['label'],x['view']) for x in items],[('설정','config')])
        self.assertEqual(len(pending),2)
        self.assertEqual(excluded['동일 요소의 변형'],1)
        self.assertEqual(excluded['UI 요소와 연결되지 않은 문자열'],1)
