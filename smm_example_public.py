from nintendo.nex import backend, authentication, ranking, datastore, datastoresmm, common
from nintendo.games import SMM
from nintendo import account
import requests
import hashlib
import struct

import logging
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.DEBUG,
    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

#Device id can be retrieved with a call to MCP_GetDeviceId on the Wii U
#Serial number can be found on the back of the Wii U
DEVICE_ID = 1145749943
SERIAL_NUMBER = "FW405593268"
SYSTEM_VERSION = 230
REGION = 4
COUNTRY = "PL"
DEVICE_CERT = "AAEABQBKDbEvwTrwca7tBJ6CSgUFeSSMaqtq/dk9hLvLggCB0yJ0FEl0zZW+5rc4A74MiGgoH7MGg4aMIfdasgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABSb290LUNBMDAwMDAwMDMtTVMwMDAwMDAxMgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAk5HNDQ0YWMxYjcAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB1+zkOANbcA8QS4OrWZQZiiJ1NW1rBMUOW8aHTQRV59LlJALysosIiEw+lcwYPWZYk/lkYBTRrqWGdtZDvCPVqAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

USERNAME = "dummy" #Nintendo network id
plain_password = "password"
pid = 1337
data = struct.pack("<I", pid) + b"\x02\x65\x43\x46" + plain_password.encode("ascii")
PASSWORD = hashlib.sha256(data).digest().hex()

SYSTEM_TITLE_ID = 0x0005001010040200
#SYSTEM_UNIQUE_ID = 0x402
SYSTEM_APPLICATION_VERSION = 0xC4

api = account.AccountAPI()
api.headers["Host"] = "account.nintendo.net"
api.baseurl = "http://127.0.0.1/v1/api/"
api.set_device(DEVICE_ID, SERIAL_NUMBER, SYSTEM_VERSION, REGION, COUNTRY, DEVICE_CERT)
api.set_title(SYSTEM_TITLE_ID, SYSTEM_APPLICATION_VERSION)
api.login(USERNAME, PASSWORD, hash=True)

SMM_TITLE_ID = 0x000500001018DD00
SMM_VERSION = 0x110
SMM_GAME_SERVER_ID = 0x1018DB00

api.set_title(SMM_TITLE_ID, SMM_VERSION)
nex_token_smm = api.get_nex_token(SMM_GAME_SERVER_ID)

backendclient = backend.BackEndClient()
backendclient.configure(SMM.ACCESS_KEY, SMM.NEX_VERSION)
backendclient.connect(nex_token_smm.host, nex_token_smm.port)

auth_info = authentication.AuthenticationInfo()
auth_info.token = nex_token_smm.token
auth_info.server_version = SMM.SERVER_VERSION

backendclient.login(nex_token_smm.username, nex_token_smm.password, auth_info, None)

client = datastoresmm.DataStoreSmmClient(backendclient.secure_client)

courseids = [
    10000000000,
]


def save_stream(outfile, func):
    settings = backend.Settings("default.cfg")
    settings.set("nex.access_key", SMM.ACCESS_KEY)
    settings.set("nex.version", SMM.NEX_VERSION)
    settings.set("prudp.ping_timeout", 10.0)
    stream = common.streams.StreamOut(settings)
    func(stream)
    f = open(outfile, "wb")
    f.write(stream.get())
    f.close()


def make_metaparam(miipid):
    param = datastoresmm.DataStoreGetMetaParam()
    param.data_id = 0
    param.persistence_target = datastoresmm.PersistenceTarget()
    param.persistence_target.owner_id = miipid
    param.persistence_target.persistence_id = 0
    param.access_password = 0
    param.result_option = 4
    return param


final_courseurls = []

i = 0
while i < len(courseids):
    courseparam = datastoresmm.MethodParam50()
    courseparam.magic = 0
    courseparam.unk = 0x27
    courseparam.data_ids = []
    for j in range(0, min(len(courseids) - i, 50)):
        courseparam.data_ids.append(courseids[i + j])
    courseresponse = client.get_custom_ranking_by_data_id(courseparam)
    coursedatas, courseresults = courseresponse.infos, courseresponse.results
    save_stream("coursedatas{}.bin".format(i), lambda s: s.list(coursedatas, s.add))

    miiparams = []
    coursedata: datastoresmm.DataStoreInfoStuff
    for coursedata in coursedatas:
        miiparams.append(make_metaparam(coursedata.info.owner_id))

    miiresponse = client.get_metas_multiple_param(miiparams)
    miidatas, miiresults = miiresponse.infos, miiresponse.results
    save_stream("miidatas{}.bin".format(i), lambda s: s.list(miidatas, s.add))

    smm_miiparam = datastoresmm.MethodParam50()
    smm_miiparam.magic = 0x11E1A300
    smm_miiparam.unk = 0x27
    smm_miiparam.data_ids = []
    miidata: datastoresmm.DataStoreMetaInfo
    for miidata in miidatas:
        smm_miiparam.data_ids.append(miidata.data_id)

    smm_miiresponse = client.get_custom_ranking_by_data_id(smm_miiparam)
    smm_miidatas, smm_miiresults = smm_miiresponse.infos, smm_miiresponse.results
    save_stream("smm_miidatas{}.bin".format(i), lambda s: s.list(smm_miidatas, s.add))

    for coursedata in coursedatas:
        req_param = datastoresmm.DataStorePrepareGetParam()
        req_param.data_id = coursedata.info.data_id
        req_param.lock_id = 0
        req_param.persistence_target = datastoresmm.PersistenceTarget()
        req_param.persistence_target.owner_id = 0
        req_param.persistence_target.persistence_id = 0xFFFF
        req_param.access_password = 0
        req_param.extra_data = ["WUP", "4", "EUR", "97", "PL", ""]
        req_info: datastoresmm.DataStoreReqGetInfo
        req_info = client.prepare_get_object(req_param)
        final_courseurls.append(req_info.url)
        logger.info("data_id: {}, URL: {}".format(req_param.data_id, req_info.url))

        rankingparam = datastoresmm.UnknownStruct2()
        rankingparam.data_id = coursedata.info.data_id
        rankingparam.unk2 = 0
        rankingdata: datastoresmm.CourseRecordInfo
        rankingdata = client.get_course_record(rankingparam)
        save_stream("ranking{}.bin".format(coursedata.info.data_id), lambda s: s.add(rankingdata))

        best_miiparams = [make_metaparam(rankingdata.world_record_pid), make_metaparam(rankingdata.first_clear_pid)]
        best_miiresponse = client.get_metas_multiple_param(best_miiparams)
        best_miis, best_results = best_miiresponse.infos, best_miiresponse.results
        save_stream("best_miidatas{}.bin".format(coursedata.info.data_id), lambda s: s.list(best_miis, s.add))

        smm_miiparam = datastoresmm.MethodParam50()
        smm_miiparam.magic = 0x11E1A300
        smm_miiparam.unk = 0x27
        smm_miiparam.data_ids = []
        miidata: datastoresmm.DataStoreMetaInfo
        for miidata in best_miis:
            smm_miiparam.data_ids.append(miidata.data_id)

        best_smm_miiresponse = client.get_custom_ranking_by_data_id(smm_miiparam)
        best_smm_miidatas, best_smm_miiresults = best_smm_miiresponse.infos, best_smm_miiresponse.results
        save_stream("best_smm_miidatas{}.bin".format(coursedata.info.data_id), lambda s: s.list(best_smm_miidatas, s.add))

        unkparam = datastoresmm.UnknownStruct4()
        unkparam.data_id = coursedata.info.data_id
        unkparam.unk2 = 3
        unkdatas = client.get_buffer_queue(unkparam)
        save_stream("unkdatas{}.bin".format(coursedata.info.data_id), lambda s: s.list(unkdatas, s.qbuffer))

    i += 50

f = open("final_courseurls.txt", "wb")
for url in final_courseurls:
    f.write(url.encode("utf8"))
    f.write(b"\n")
f.close()

backendclient.close() 