"""Public update checks never use monitored repos, credentials or run commands."""
import queue
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from night_bandit_desktop.updates import check_updates,version_key,UpdateError,public_json
from night_bandit_desktop.update_ui import UpdateService
from night_bandit_desktop.ui import Desktop
import tempfile
from pathlib import Path

REPO='kjohnsonthecoder/Night-Bandit'
def response(tag='v0.4.0',**changes):return {'tag_name':tag,'draft':False,'prerelease':False,**changes}
def fixture(release=None,metadata=None):
    def fetch(url):
        if url.endswith('/releases/latest'):
            if release is None:raise LookupError('not_found')
            return release
        return metadata or {'full_name':REPO,'private':False,'default_branch':'main'}
    return fetch

class PublicVersionTests(unittest.TestCase):
    def test_numeric_and_prerelease_precedence(self):
        for old,new in [('0.3.9','0.3.10'),('0.9.9','0.10.0'),('1.2.3-rc.2','1.2.3'),('1.2.3-rc.2','1.2.3-rc.10'),('1.2.3-1','1.2.3-alpha')]:
            with self.subTest(old=old,new=new):self.assertLess(version_key(old),version_key(new))
        self.assertEqual(version_key('v1.2.3+build.1'),version_key('1.2.3+build.2'))
    def test_malformed_version_never_becomes_a_command(self):
        for tag in ['v0.4.0; start evil','0.4','v01.2.3','1.2.3-01','https://evil.invalid','x'*101]:
            with self.subTest(tag=tag):self.assertEqual(check_updates(repo=REPO,fetch=fixture(response(tag))).status,'error')
    def test_new_current_and_ahead_are_distinct(self):
        self.assertEqual(check_updates('0.3.0',REPO,fixture(response())).status,'available')
        self.assertEqual(check_updates('0.4.0',REPO,fixture(response())).status,'current')
        self.assertEqual(check_updates('0.5.0',REPO,fixture(response())).status,'ahead')
    def test_no_release_is_not_up_to_date(self):
        result=check_updates('0.3.0',REPO,fixture());self.assertEqual(result.status,'no_release');self.assertFalse(result.latest);self.assertFalse(result.instructions)
    def test_unconfigured_source_never_requests_network(self):
        def never(url):raise AssertionError('No request allowed')
        self.assertEqual(check_updates(repo=None,fetch=never).status,'not_configured')
    def test_private_operations_and_test_sources_never_requested(self):
        def never(url):raise AssertionError('Private/test repository must never be queried')
        for repo in ['example/private-operations','example/test-fixture','https://evil.invalid/x','owner/repo;code']:
            self.assertEqual(check_updates(repo=repo,fetch=never).status,'error')
    def test_private_or_changed_identity_or_branch_rejected(self):
        for metadata in [{'full_name':REPO,'private':True,'default_branch':'main'},{'full_name':'elsewhere/other','private':False,'default_branch':'main'},{'full_name':REPO,'private':False,'default_branch':'other'}]:
            self.assertEqual(check_updates(repo=REPO,fetch=fixture(response(),metadata)).status,'error')
    def test_drafts_and_preview_releases_not_announced_as_stable(self):
        for r in [response(draft=True),response(prerelease=True),response('v0.4.0-rc.1')]:self.assertEqual(check_updates(repo=REPO,fetch=fixture(r)).status,'error')
    def test_untrusted_release_url_and_body_are_not_used(self):
        result=check_updates(repo=REPO,fetch=fixture(response(html_url='https://evil.invalid',body='run attacker commands')))
        self.assertEqual(result.release_url,'https://github.com/'+REPO+'/releases/tag/v0.4.0');self.assertNotIn('attacker',result.instructions)
    def test_instructions_do_not_execute_git_or_change_files(self):
        with patch('subprocess.run',side_effect=AssertionError('No commands')),patch('subprocess.Popen',side_effect=AssertionError('No commands')):
            result=check_updates(repo=REPO,fetch=fixture(response()))
        self.assertIn('git pull --ff-only origin main',result.instructions);self.assertIn('git status --short',result.instructions);self.assertIn('Stop Automatic Sync and Exit',result.instructions);self.assertIn('not use a repository or file that NIGHT BANDIT monitors',result.instructions);self.assertNotIn('reset --hard',result.instructions)
    def test_unavailable_and_connection_errors_have_no_update_claim(self):
        def missing(url):raise LookupError()
        def offline(url):raise UpdateError('offline')
        self.assertEqual(check_updates(repo=REPO,fetch=missing).status,'unavailable');self.assertEqual(check_updates(repo=REPO,fetch=offline).status,'error')
    def test_requests_are_anonymous_bounded_and_use_fixed_endpoint(self):
        class Reply:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,count):self.count=count;return b'{"private":false}'
        reply=Reply()
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.return_value=reply;public_json('https://api.github.com/repos/'+REPO)
            call=build.return_value.open.call_args;request=call.args[0]
            self.assertNotIn('authorization',{k.lower() for k in request.headers});self.assertEqual(call.kwargs['timeout'],8);self.assertEqual(reply.count,512*1024+1)

class UpdateUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='nb-update-ui-');self.root=tk.Tk();self.root.withdraw();self.app=Desktop(self.root,Path(self.temp.name));self.errors=[];self.root.report_callback_exception=lambda *a:self.errors.append(str(a))
    def tearDown(self):
        if hasattr(self.app,'update_service') and self.app.update_service.job:self.app.update_service.job.join(3)
        self.app._busy=False;self.root.destroy();self.temp.cleanup();self.assertEqual(self.errors,[])
    def wait(self,test):
        end=time.monotonic()+4
        while not test() and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertTrue(test());self.assertEqual(self.errors,[])
    def test_available_result_displays_manual_instructions_while_sync_gate_is_busy(self):
        self.app.update_service=UpdateService(lambda:check_updates(repo=REPO,fetch=fixture(response())))
        self.app._busy=True;self.app.settings();panel=self.app.update_panel;panel.check.invoke();self.wait(lambda:bool(panel.instructions))
        self.assertTrue(self.app._busy);self.assertIn('0.4.0',panel.status.get());self.assertFalse(panel.copy.disabled)
    def test_only_one_check_inflight_and_navigation_does_not_start_another(self):
        release=threading.Event();calls=[]
        def checker():calls.append(1);release.wait(2);return check_updates(repo=REPO,fetch=fixture())
        self.app.update_service=UpdateService(checker);self.app.settings();self.app.update_panel.start();self.app.update_panel.start();self.app.home();self.app.settings();self.assertEqual(calls,[1]);release.set();self.wait(lambda:'No published' in self.app.update_panel.status.get());self.assertEqual(calls,[1])
    def test_closed_view_does_not_receive_late_tk_callbacks(self):
        release=threading.Event()
        self.app.update_service=UpdateService(lambda:(release.wait(2),check_updates(repo=REPO,fetch=fixture()))[1]);self.app.settings();panel=self.app.update_panel;panel.start();self.app.home();self.assertTrue(panel.closed);release.set();self.app.update_service.job.join(2);self.root.update();self.assertEqual(self.errors,[])
    def test_startup_and_settings_never_automatically_contact_github(self):
        calls=[];self.app.update_service=UpdateService(lambda:calls.append(1));self.app.settings();self.root.update();self.assertEqual(calls,[]);self.assertFalse(self.app.update_panel.copy.disabled is False)

if __name__=='__main__':unittest.main()
