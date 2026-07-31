import json
import os
import tempfile
import unittest
from types import MethodType, SimpleNamespace

import smmdb
from example_smm_server import DataStoreSmmServer
from smm_dataprovider import SmmDataProvider
from nintendo.nex.prudp import PRUDPPacket, PRUDPStream, TYPE_DATA


class CourseSourceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_paths = (
            smmdb.BASE_CW_DIR,
            smmdb.BASE_SMMDB_DIR,
            smmdb.SETTINGS_INI_PATH,
        )
        smmdb.BASE_CW_DIR = os.path.join(self.tempdir.name, "courseworld")
        smmdb.BASE_SMMDB_DIR = os.path.join(self.tempdir.name, "smmdb")
        smmdb.SETTINGS_INI_PATH = os.path.join(self.tempdir.name, "settings.ini")
        for base in (smmdb.BASE_CW_DIR, smmdb.BASE_SMMDB_DIR):
            for difficulty in range(4):
                os.makedirs(os.path.join(base, str(difficulty)))

        self.provider = SmmDataProvider.__new__(SmmDataProvider)
        self.provider.get_course_data = MethodType(
            lambda _self, course_id: course_id, self.provider
        )

    def tearDown(self):
        (
            smmdb.BASE_CW_DIR,
            smmdb.BASE_SMMDB_DIR,
            smmdb.SETTINGS_INI_PATH,
        ) = self.old_paths
        self.tempdir.cleanup()

    def select_source(self, source):
        with open(smmdb.SETTINGS_INI_PATH, "w", encoding="utf-8") as config:
            config.write("[General]\nCourseSource={}\n".format(source))

    def add_course(self, base, folder, course_id, difficulty):
        path = os.path.join(base, str(folder), "{:011d}-00001".format(course_id))
        with open(path, "wb") as course:
            course.write(b"course")
        with open(path + ".json", "w", encoding="utf-8") as metadata:
            json.dump({"difficulty": difficulty}, metadata)

    def test_courseworld_uses_mixed_pool_for_every_difficulty(self):
        self.add_course(smmdb.BASE_CW_DIR, 1, 1, 1)
        self.add_course(smmdb.BASE_CW_DIR, 1, 2, 1)
        self.add_course(smmdb.BASE_CW_DIR, 1, smmdb.LEGACY_SYSTEM_ID, 1)
        self.select_source("CourseWorld")

        courses = self.provider.get_random_courses_by_difficulty(3, 50)

        self.assertEqual(set(courses), {1, 2})

    def test_smmdb_uses_metadata_not_legacy_folder_placement(self):
        self.add_course(smmdb.BASE_SMMDB_DIR, 1, 10, 3)
        self.add_course(smmdb.BASE_SMMDB_DIR, 3, 11, 1)
        self.add_course(smmdb.BASE_SMMDB_DIR, 3, 12, 3)
        self.select_source("SMMDB")

        courses = self.provider.get_random_courses_by_difficulty(3, 50)

        self.assertEqual(set(courses), {10, 12})

    def test_empty_course_ranking_request_stays_empty(self):
        server = DataStoreSmmServer.__new__(DataStoreSmmServer)
        param = SimpleNamespace(unk=0x27, magic=0, data_ids=[])

        response = server.get_custom_ranking_by_data_id(None, param)

        self.assertEqual(response.infos, [])
        self.assertEqual(response.results, [])

    def test_requested_course_gets_metadata_and_record(self):
        class Provider:
            def get_course_data(self, course_id):
                return "course-{}".format(course_id) if course_id == 53 else None

            def get_ranking(self, course_id):
                return None

        server = DataStoreSmmServer.__new__(DataStoreSmmServer)
        server.data_provider = Provider()
        request = SimpleNamespace(data_id=53, unk2=0)

        response = server.get_metas_with_course_record(
            None, [request], SimpleNamespace()
        )

        self.assertEqual(response.infos, ["course-53"])
        self.assertEqual(response.unknown[0].data_id, 53)
        self.assertEqual(len(response.results), 1)


class PRUDPTimeoutTests(unittest.TestCase):
    def test_late_timeout_after_ack_is_ignored(self):
        stream = PRUDPStream.__new__(PRUDPStream)
        stream.ack_events = {}
        stream.resend_limit = 3
        packet = PRUDPPacket(TYPE_DATA)
        packet.stream_id = 0
        packet.packet_id = 16

        stream.handle_timeout((packet, 1))
        stream.acknowledge((TYPE_DATA, 0, 16), packet)


if __name__ == "__main__":
    unittest.main()
