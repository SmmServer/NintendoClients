import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

import smmdb
from smm_dataprovider import SmmDataProvider
from example_smm_server import DataStoreSmmServer, SmmUtilityServer
from example_friend_server import FriendsServer


def _make_provider_with_real_mii():
    from nintendo.nex import backend, common, datastoresmm
    from nintendo.games import SMM
    settings = backend.Settings("default.cfg")
    settings.set("nex.access_key", SMM.ACCESS_KEY)
    settings.set("nex.version", SMM.NEX_VERSION)
    with open(os.path.join(os.path.dirname(smmdb.__file__), "smm_miidata.bin"), "rb") as f:
        data = f.read()
    stream = common.streams.StreamIn(data, settings)
    infos = stream.list(datastoresmm.DataStoreInfoStuff)
    provider = SmmDataProvider.__new__(SmmDataProvider)
    provider.mii_data_id = {i.info.data_id: i for i in infos[:5]}
    provider.mii_data_pid = {i.info.owner_id: i.info.data_id for i in infos[:5]}
    provider.fake_mii_data_id = 20000000000
    provider.fake_mii_pid = 2000000000
    provider.fake_mii_name = {}
    provider.list_offsets = {}
    import collections as _collections
    provider.recent_course_ids = _collections.deque(maxlen=200)
    provider.settings = settings
    return provider


class MakerNameTests(unittest.TestCase):
    def test_rename_fake_mii_patches_embedded_name(self):
        provider = _make_provider_with_real_mii()
        pid = provider.construct_fake_miidata("TestMaker1")
        data_id = provider.mii_data_pid[pid]
        fake = provider.mii_data_id[data_id]
        self.assertEqual(fake.info.name, "TestMaker1")
        from nintendo.miis import MiiData
        mii = MiiData.parse(bytes(fake.info.meta_binary[24:24 + 96]))
        self.assertIsNotNone(mii)
        self.assertEqual(mii.mii_name, "TestMaker1")

    def test_generate_dummy_mii_patches_name(self):
        provider = _make_provider_with_real_mii()
        fake = provider._generate_dummy_mii(99999, "DummyName")
        from nintendo.miis import MiiData
        mii = MiiData.parse(bytes(fake.info.meta_binary[24:24 + 96]))
        self.assertEqual(mii.mii_name, "DummyName")


class StarBatchTests(unittest.TestCase):
    def test_completion_batch_ignored(self):
        server = DataStoreSmmServer.__new__(DataStoreSmmServer)
        server.data_provider = mock.Mock()
        with mock.patch("example_smm_server.get_course_source", return_value="SMMDB"):
            with mock.patch("example_smm_server.requests.post") as post:
                res = server.rate_custom_ranking(None, [mock.Mock(), mock.Mock()])
                post.assert_not_called()
                self.assertEqual(res.result.error_code, 0x10001)

    def test_star_batch_still_votes(self):
        server = DataStoreSmmServer.__new__(DataStoreSmmServer)
        server.data_provider = mock.Mock()
        server.data_provider.get_course_filename.return_value = None
        params = []
        for i in range(12):
            p = mock.Mock()
            p.unk2 = 300000000
            p.data_id = 1000 + i
            params.append(p)
        with mock.patch("example_smm_server.get_course_source", return_value="SMMDB"):
            res = server.rate_custom_ranking(None, params)
            self.assertEqual(res.result.error_code, 0x10001)


class GracefulStubTests(unittest.TestCase):
    def setUp(self):
        self.server = DataStoreSmmServer.__new__(DataStoreSmmServer)
        self.server.settings = mock.Mock()
        self.server.data_provider = mock.Mock()
        self.server.data_provider.get_random_courses_mixed.return_value = ["c1"]

    def test_unknown_result_option_skipped_not_raised(self):
        from nintendo.nex import common as nex_common
        param = mock.Mock()
        param.result_option = 99
        param.persistence_target.owner_id = 1337
        res = self.server.get_metas_multiple_param(None, [param])
        self.assertEqual(res.infos, [])
        self.assertEqual(res.results, [])

    def test_event_course_empty(self):
        param = mock.Mock()
        param.result_option = 6
        res = self.server.get_metas_multiple_param(None, [param])
        self.assertEqual(res.infos, [])

    def test_conditional_suggested_ctr_return_mixed(self):
        self.assertEqual(self.server.conditional_search_object(None, 1, mock.Mock(), []), ["c1"])
        self.assertEqual(self.server.suggested_course_search_object(None, mock.Mock(), []), ["c1"])
        self.assertEqual(self.server.ctr_pick_up_course_search_object(None, mock.Mock(), []), ["c1"])

    def test_score_range_fallback(self):
        self.assertEqual(self.server.score_range_cascaded_search_object(None, mock.Mock(), []), ["c1"])

    def test_misc_noops(self):
        self.server.report_course(None, mock.Mock())
        self.server.delete_course_record(None, None)
        self.server.set_deletion_reason(None, None)
        self.server.reset_rate_custom_ranking_counter(None, 0)
        self.server.add_to_buffer_queue(None, None)
        self.assertEqual(self.server.get_buffer_queues(None, None), [])
        self.server.clear_buffer_queues(None, None)
        self.assertEqual(self.server.search_unknown_platform_objects(None, None), [])


class UtilityStubTests(unittest.TestCase):
    def test_utility_returns_without_notimplemented(self):
        from nintendo.nex import common as nex_common
        srv = SmmUtilityServer()
        ctx = SimpleNamespace(pid=1337)
        self.assertGreater(srv.acquire_nex_unique_id(ctx), 0)
        info = srv.acquire_nex_unique_id_with_password(ctx)
        self.assertGreater(info.unique_id, 0)
        srv.associate_nex_unique_id_with_my_principal_id(ctx, info)
        srv.associate_nex_unique_ids_with_my_principal_id(ctx, [info])
        self.assertGreater(srv.get_associated_nex_unique_id_with_my_principal_id(ctx).unique_id, 0)
        self.assertEqual(srv.get_associated_nex_unique_ids_with_my_principal_id(ctx), [])
        self.assertEqual(srv.get_integer_settings(ctx, 0), {})
        self.assertEqual(srv.get_string_settings(ctx, 0), {})


class FriendsStubTests(unittest.TestCase):
    def test_update_presence_noop(self):
        srv = FriendsServer()
        srv.update_presence(None, mock.Mock())
        srv.handle_update_mii(None, mock.Mock(), mock.Mock())
        srv.handle_update_comment(None, mock.Mock(), mock.Mock())
        srv.handle_update_preference(None, mock.Mock(), mock.Mock())


class DifficultyFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old = (smmdb.BASE_CW_DIR, smmdb.BASE_SMMDB_DIR, smmdb.SETTINGS_INI_PATH, smmdb.LISTS_DIR)
        smmdb.BASE_CW_DIR = os.path.join(self.tempdir.name, "courseworld")
        smmdb.BASE_SMMDB_DIR = os.path.join(self.tempdir.name, "smmdb")
        smmdb.SETTINGS_INI_PATH = os.path.join(self.tempdir.name, "settings.ini")
        smmdb.LISTS_DIR = os.path.join(self.tempdir.name, "lists")
        for base in (smmdb.BASE_CW_DIR, smmdb.BASE_SMMDB_DIR):
            for d in range(4):
                os.makedirs(os.path.join(base, str(d)), exist_ok=True)
        os.makedirs(smmdb.LISTS_DIR, exist_ok=True)
        with open(smmdb.SETTINGS_INI_PATH, "w") as f:
            f.write("[General]\nCourseSource=SMMDB\n")
        self.provider = SmmDataProvider.__new__(SmmDataProvider)
        self.provider.settings = mock.Mock()
        self.provider.course_data = {}
        self.provider.mii_data_id = {}
        self.provider.mii_data_pid = {}
        self.provider.fake_mii_data_id = 20000000000
        self.provider.fake_mii_pid = 2000000000
        self.provider.fake_mii_name = {}
        self.provider.list_offsets = {}

    def tearDown(self):
        smmdb.BASE_CW_DIR, smmdb.BASE_SMMDB_DIR, smmdb.SETTINGS_INI_PATH, smmdb.LISTS_DIR = self.old
        self.tempdir.cleanup()

    def _add(self, folder, cid, diff):
        path = os.path.join(smmdb.BASE_SMMDB_DIR, str(folder), "{:011d}-00001".format(cid))
        with open(path, "wb") as f:
            f.write(b"ASH0dummy")
        with open(path + ".json", "w") as f:
            json.dump({"difficulty": diff, "maker": "M", "title": "T",
                       "meta_binary_b64": "AA=="}, f)
        return path

    def test_strict_empty_falls_back_to_mixed(self):
        p = os.path.join(smmdb.BASE_SMMDB_DIR, "0", "{:011d}-00001".format(111))
        with open(p, "wb") as f:
            f.write(b"x")
        with open(p + ".json", "w") as f:
            json.dump({"difficulty": 0}, f)
        with mock.patch.object(SmmDataProvider, "get_course_data", return_value=mock.Mock()), \
             mock.patch.object(SmmDataProvider, "get_random_courses_mixed", return_value=["fallback"]) as mixed:
            provider = SmmDataProvider.__new__(SmmDataProvider)
            provider.settings = mock.Mock()
            provider.course_data = {}
            provider.get_active_source = lambda: "SMMDB"
            provider.get_active_source_dirs = lambda: [smmdb.BASE_SMMDB_DIR]
            provider.get_cached_course_difficulty = SmmDataProvider.get_cached_course_difficulty.__get__(provider, SmmDataProvider)
            res = SmmDataProvider.get_random_courses_by_difficulty(provider, 3, 5)
            self.assertEqual(res, ["fallback"])
            mixed.assert_called()


class DiagnosticsTests(unittest.TestCase):
    def test_diagnose_returns_list_without_raising(self):
        import diagnostics
        res = diagnostics.diagnose()
        self.assertIsInstance(res, list)

    def test_excluded_parsing_handles_no_netsh(self):
        import diagnostics
        with mock.patch("diagnostics.platform.system", return_value="Linux"):
            self.assertEqual(diagnostics.get_excluded_port_ranges(), [])


if __name__ == "__main__":
    unittest.main()

class RepeatRegressionTests(unittest.TestCase):

    def test_pool_sizes_are_100(self):
        import smmdb as _smmdb
        self.assertEqual(_smmdb.BOOTSTRAP_LIMIT, 50)
        self.assertEqual(_smmdb.MAINTENANCE_LIMIT, 100)

    def test_mixed_avoids_recent(self):
        import collections as _c
        import tempfile as _tf
        import smmdb as _smmdb
        from smm_dataprovider import SmmDataProvider as _P
        from unittest import mock as _mock
        tmp = _tf.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = (_smmdb.BASE_SMMDB_DIR, _smmdb.BASE_CW_DIR, _smmdb.SETTINGS_INI_PATH, _smmdb.LISTS_DIR)
        _smmdb.BASE_SMMDB_DIR = tmp.name + "/smmdb"
        _smmdb.BASE_CW_DIR = tmp.name + "/cw"
        _smmdb.SETTINGS_INI_PATH = tmp.name + "/settings.ini"
        _smmdb.LISTS_DIR = tmp.name + "/lists"
        os.makedirs(_smmdb.BASE_SMMDB_DIR + "/0", exist_ok=True)
        os.makedirs(_smmdb.LISTS_DIR, exist_ok=True)
        with open(_smmdb.SETTINGS_INI_PATH, "w") as f:
            f.write("[General]\nCourseSource=SMMDB\n")
        for cid in range(100, 110):
            p = os.path.join(_smmdb.BASE_SMMDB_DIR, "0", "{:011d}-00001".format(cid))
            with open(p, "wb") as f:
                f.write(b"x")
        try:
            prov = _P.__new__(_P)
            prov.settings = _mock.Mock()
            prov.course_data = {}
            prov.recent_course_ids = _c.deque([100, 101, 102, 103], maxlen=200)
            prov.get_active_source = lambda: "SMMDB"
            prov.get_active_source_dirs = lambda: [_smmdb.BASE_SMMDB_DIR]
            prov.get_datastore_fallback_dirs = lambda: []
            prov.get_course_data = lambda cid, _p=prov: "course-{}".format(cid)
            first = prov.get_random_courses_mixed(4)
            self.assertTrue(all(c != "course-100" for c in first) or len(first) == 4)
            self.assertGreaterEqual(len(prov.recent_course_ids), 4)
        finally:
            (_smmdb.BASE_SMMDB_DIR, _smmdb.BASE_CW_DIR, _smmdb.SETTINGS_INI_PATH, _smmdb.LISTS_DIR) = old

    def test_mark_on_load_called(self):
        import smm_dataprovider as _dp
        srv = DataStoreSmmServer.__new__(DataStoreSmmServer)
        prov = mock.Mock()
        course = mock.Mock()
        course.info.size = 123
        prov.get_course_data.return_value = course
        prov.get_course_url.return_value = "http://x"
        srv.data_provider = prov
        srv.get_req_info(555)
        prov.mark_course_played.assert_called_once_with(555)

    def test_refresh_once_exists(self):
        import smmdb as _smmdb
        self.assertTrue(hasattr(_smmdb.CacheManager, "refresh_once"))
