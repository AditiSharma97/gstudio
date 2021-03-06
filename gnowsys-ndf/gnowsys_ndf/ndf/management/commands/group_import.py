import os
import json
import imp
import subprocess
from bson import json_util
import pathlib2
try:
    from bson import ObjectId
except ImportError:  # old pymongo
    from pymongo.objectid import ObjectId

import time
import datetime

# from bson.json_util import dumps,loads,object_hook
from django.core.management.base import BaseCommand, CommandError
from gnowsys_ndf.ndf.models  import node_collection, triple_collection, filehive_collection, counter_collection
from gnowsys_ndf.ndf.models import HistoryManager, RCS
from gnowsys_ndf.settings import GSTUDIO_DATA_ROOT, GSTUDIO_LOGS_DIR_PATH, MEDIA_ROOT, GSTUDIO_INSTITUTE_ID, RCS_REPO_DIR
from users_dump_restore import load_users_dump
from schema_mapping import update_factory_schema_mapper
from gnowsys_ndf.ndf.views.utils import replace_in_list, merge_lists_and_maintain_unique_ele

# global variables declaration
DATA_RESTORE_PATH = None
DATA_DUMP_PATH = None
DEFAULT_USER_ID = None
DEFAULT_USER_SET = False
USER_ID_MAP = {}
SCHEMA_ID_MAP = {}
log_file = None
CONFIG_VARIABLES = None
DATE_AT_IDS = []
date_related_at_cur = node_collection.find({'_type': 'AttributeType', 
    'name': {'$in': ["start_time", "end_time", "start_enroll", "end_enroll"]}})
for each_date_related_at in date_related_at_cur:
    DATE_AT_IDS.append(each_date_related_at._id)
'''
Following will be available:
    CONFIG_VARIABLES.FORK=True
    CONFIG_VARIABLES.CLONE=False
    CONFIG_VARIABLES.RESTORE_USER_DATA=True
    CONFIG_VARIABLES.GSTUDIO_INSTITUTE_ID='MZ-10'
    CONFIG_VARIABLES.GROUP_ID='58dded48cc566201992f6e79'
    CONFIG_VARIABLES.MD5='aeba0e3629fb0443861c699ae327d962'
'''

def create_log_file(restore_path):
    '''
        Creates log file in gstudio-logs/ with 
        the name of the dump folder
    '''
    restore_path = restore_path.split("/")[-1]
    log_file_name = 'group_restore_of_dump_' + str(CONFIG_VARIABLES.GROUP_ID)+ '.log'
    if not os.path.exists(GSTUDIO_LOGS_DIR_PATH):
        os.makedirs(GSTUDIO_LOGS_DIR_PATH)

    log_file_path = os.path.join(GSTUDIO_LOGS_DIR_PATH, log_file_name)
    global log_file
    log_file = open(log_file_path, 'w+')
    log_file.write("\n######### Script ran on : " + str(datetime.datetime.now()) + " #########\n\n")
    return log_file_path


def call_exit():
    print "\n Exiting..."
    os._exit(0)

def read_config_file():
    """
    Read migration_configs.py file generated during
     the export of group and load the variables in 
     CONFIG_VARIABLES to be accessible in entire program
    """
    global CONFIG_VARIABLES
    CONFIG_VARIABLES = imp.load_source('config_variables',
            os.path.join(DATA_RESTORE_PATH,'migration_configs.py'))

def validate_data_dump():
    """
    For validation of the exported dump and the 
     importing data-dump, calculate MD5 and
     check with CONFIG_VARIABLES.MD5
     This will ensure the exported data is NOT altered
     before importing
    """
    global log_file
    from checksumdir import dirhash
    md5hash = dirhash(DATA_DUMP_PATH, 'md5')
    if CONFIG_VARIABLES.MD5 != md5hash:
        print "\n MD5 NOT matching."
        proceed_without_validation = raw_input("MD5 not matching. Restoration not recommended.\n \
                        Would you still like to continue ?")
        if proceed_without_validation != 'y' and proceed_without_validation != 'Y':
            log_file.write("\n Checksum validation Failed on dump data")
            call_exit()
    else:
        log_file.write("\n Checksum validation Success on dump data")
        print "\nValidation Success..!"

def check_group_availability():
    group_node = node_collection.one({'_id': ObjectId(CONFIG_VARIABLES.GROUP_ID)})
    global log_file
    if group_node:
        confirm_grp_data_merge = raw_input("Dump Group already exists here. Would you like to merge the data ?")
        if confirm_grp_data_merge != 'y' and confirm_grp_data_merge != 'Y':
            log_file.write("\n Group with Restore Group ID is FOUND on Target system.")
            print "\n Group with restoration ID already exists."
            call_exit()
        else:
            log_file.write("\n Group Merge confirmed.")
            print " Proceeding to restore."
    else:
        log_file.write("\n Group with Restore Group ID NOT found on Target system.")
        print "\n Group with restoration ID does not exists."
        print " Proceeding to restore."

def user_objs_restoration():
    global USER_ID_MAP
    global DEFAULT_USER_ID
    global DEFAULT_USER_SET
    global log_file
    user_json_data = None
    # print "CONFIG_VARIABLES.RESTORE_USER_DATA"
    if CONFIG_VARIABLES.RESTORE_USER_DATA:
        user_dump_restore = raw_input("\n\tUser dump is available.  \
            Would you like to restore it (y/n) ?: ")
        if user_dump_restore == 'y' or user_dump_restore == 'Y':
            log_file.write("\n Request for users restoration : Yes.")

            user_json_file_path = os.path.join(DATA_DUMP_PATH, 'users_dump.json')
            with open(user_json_file_path, 'rb+') as user_json_fin:
                user_json_data = json.loads(user_json_fin.read())
                print "\n Restoring Users. Please wait.."
                USER_ID_MAP = load_users_dump(DATA_DUMP_PATH, user_json_data)
                log_file.write("\n USER_ID_MAP: "+ str(USER_ID_MAP))
                print "\n Completed Restoring Users."
        else:
            log_file.write("\n Request for users restoration : No.")
            DEFAULT_USER_SET = True
            default_user_confirmation = raw_input("\n\tRestoration will use default usre-id=1. \
            \n\tEnter y to continue, or n if you want to use some other id?: ")
            if default_user_confirmation == 'y' or default_user_confirmation == 'Y':
                log_file.write("\n Request for Default user with id=1 : Yes.")
                DEFAULT_USER_ID = 1
            else:
                log_file.write("\n Request for Default user with id=1 : No.")
                DEFAULT_USER_ID = int(raw_input("Enter user-id: "))
                log_file.write("\n Request for Setting Default user with id :" + str(DEFAULT_USER_SET))



def update_schema_id_for_triple(document_json):
    if SCHEMA_ID_MAP:
        if 'relation_type' in document_json and document_json['relation_type'] in SCHEMA_ID_MAP:
            document_json['relation_type'] = SCHEMA_ID_MAP[document_json['relation_type']]
        if 'attribute_type' in document_json and document_json['attribute_type'] in SCHEMA_ID_MAP:
            document_json['attribute_type'] = SCHEMA_ID_MAP[document_json['attribute_type']]
        return document_json

def update_schema_and_user_ids(document_json):
    if SCHEMA_ID_MAP:
        if document_json['member_of']:
            for each_mem_of_id in document_json['member_of']:
                if each_mem_of_id in SCHEMA_ID_MAP:
                    replace_in_list(document_json['member_of'], 
                        each_mem_of_id, SCHEMA_ID_MAP[each_mem_of_id])
        if document_json['type_of']:
            for each_mem_of_id in document_json['type_of']:
                if each_mem_of_id in SCHEMA_ID_MAP:
                    replace_in_list(document_json['type_of'], 
                        each_mem_of_id, SCHEMA_ID_MAP[each_mem_of_id])
    if USER_ID_MAP:
        if document_json['created_by'] in USER_ID_MAP:
            replace_in_list(document_json['contributors'], 
                document_json['created_by'], USER_ID_MAP[document_json['created_by']])
            document_json['created_by'] = USER_ID_MAP[document_json['created_by']]
        if document_json['modified_by'] in USER_ID_MAP:
            document_json['modified_by'] = USER_ID_MAP[document_json['modified_by']]
    return document_json

    '''
    else:
        Schema is same. No updation required.
    '''

def copy_version_file(filepath):
    if os.path.exists(filepath):
        cwd_path = os.getcwd()
        posix_filepath = pathlib2.Path(filepath)
        rcs_data_path = str(pathlib2.Path(*posix_filepath.parts[:7]))
        rcs_file_path = str(pathlib2.Path(*posix_filepath.parts[7:]))
        os.chdir(rcs_data_path)
        cp = "cp  -v " + rcs_file_path + " " +" --parents " + RCS_REPO_DIR + "/"
        subprocess.Popen(cp,stderr=subprocess.STDOUT,shell=True)
        os.chdir(cwd_path)


def restore_filehive_objects(rcs_filehives_path):
    print "\nRestoring Filehives.."
    global log_file
    log_file.write("\nRestoring Filehives. ")
    for dir_, _, files in os.walk(rcs_filehives_path):
        for filename in files:
            filepath =  os.path.join(dir_, filename)
            fh_json= get_json_file(filepath)
            fh_obj = filehive_collection.one({'_id': ObjectId(fh_json['_id'])})

            if not fh_obj:
                copy_version_file(filepath)
                log_file.write("\nRCS file copied : \n\t" + str(filepath) )
                try:
                    log_file.write("\nInserting new Filehive Object : \n\tNew-obj: " + \
                        str(fh_json))
                    node_id = filehive_collection.collection.insert(fh_json)
                    # print "\n fh_json: ", fh_json
                    fh_obj = filehive_collection.one({'_id': node_id})

                    fh_obj.save()
                    log_file.write("\nUpdate RCS using save()")
                except Exception as fh_insert_err:
                    log_file.write("\nError while inserting FH obj" + str(fh_insert_err))
                    pass
            else:
                log_file.write("\nFound Existing Filehive Object : \n\tFound-obj: " + \
                    str(fh_obj) + "\n\tExiting-obj: "+str(fh_json))

def restore_node_objects(rcs_nodes_path):
    print "\nRestoring Nodes.."
    global log_file
    log_file.write("\nRestoring Nodes. ")
    for dir_, _, files in os.walk(rcs_nodes_path):
        for filename in files:
            filepath =  os.path.join(dir_, filename)
            node_json = get_json_file(filepath)
            node_obj = node_collection.one({'_id': ObjectId(node_json['_id'])})

            if node_obj:
                log_file.write("\nFound Existing Node : " + str(node_obj._id))
                node_changed = False
                if node_obj.author_set != node_json['author_set'] and node_json['author_set']:
                    log_file.write("\n Old author_set :\n\t " + str(node_obj.author_set))
                    merge_lists_and_maintain_unique_ele(node_obj.author_set,
                        node_json['author_set'])
                    log_file.write("\n New author_set :\n\t "+ str(node_obj.author_set))
                    node_changed = True

                if node_obj.relation_set != node_json['relation_set'] and node_json['relation_set']:
                    log_file.write("\n Old relation_set :\n\t "+ str(node_obj.relation_set))
                    merge_lists_and_maintain_unique_ele(node_obj.relation_set,
                        node_json['relation_set'], advanced_merge=True)
                    log_file.write("\n New relation_set :\n\t "+ str(node_obj.relation_set))
                    node_changed = True

                if node_obj.attribute_set != node_json['attribute_set'] and node_json['attribute_set']:
                    log_file.write("\n Old attribute_set :\n\t "+ str(node_obj.attribute_set))
                    merge_lists_and_maintain_unique_ele(node_obj.attribute_set,
                        node_json['attribute_set'], advanced_merge=True)
                    log_file.write("\n New attribute_set :\n\t "+ str(node_obj.attribute_set))
                    node_changed = True

                if node_obj.post_node != node_json['post_node'] and node_json['post_node']:
                    log_file.write("\n Old post_node :\n\t "+ str(node_obj.post_node))
                    merge_lists_and_maintain_unique_ele(node_obj.post_node,
                        node_json['post_node'])
                    log_file.write("\n New post_node :\n\t "+ str(node_obj.post_node))
                    node_changed = True

                if node_obj.prior_node != node_json['prior_node'] and node_json['prior_node']:
                    log_file.write("\n Old prior_node :\n\t "+ str(node_obj.prior_node))
                    merge_lists_and_maintain_unique_ele(node_obj.prior_node,
                        node_json['prior_node'])
                    log_file.write("\n New prior_node :\n\t "+ str(node_obj.prior_node))
                    node_changed = True

                if node_obj.origin != node_json['origin'] and node_json['origin']:
                    log_file.write("\n Old origin :\n\t "+ str(node_obj.origin))
                    merge_lists_and_maintain_unique_ele(node_obj.origin,
                        node_json['origin'])
                    log_file.write("\n New origin :\n\t "+ str(node_obj.origin))
                    node_changed = True

                if node_obj.collection_set != node_json['collection_set'] and node_json['collection_set']:
                    log_file.write("\n Old collection_set :\n\t "+ str(node_obj.collection_set))
                    merge_lists_and_maintain_unique_ele(node_obj.collection_set,
                        node_json['collection_set'])
                    log_file.write("\n New collection_set :\n\t "+ str(node_obj.collection_set))
                    node_changed = True

                if node_changed:
                    log_file.write("\n Node Updated: \n\t OLD: " + str(node_obj) + "\n\tNew: "+str(node_json))
                    node_obj.save()
            else:
                copy_version_file(filepath)
                log_file.write("\n RCS file copied : \n\t" + str(filepath))
                node_json = update_schema_and_user_ids(node_json)
                try:
                    log_file.write("\n Inserting Node doc : \n\t" + str(node_json))
                    node_id = node_collection.collection.insert(node_json)
                    node_obj = node_collection.one({'_id': node_id})
                    node_obj.save(groupid=ObjectId(CONFIG_VARIABLES.GROUP_ID))
                    log_file.write("\nUpdate RCS using save()")
                except Exception as node_insert_err:
                    log_file.write("\nError while inserting Node obj" + str(node_insert_err))
                    pass


def restore_triple_objects(rcs_triples_path):
    print "\nRestoring Triples.."
    global log_file
    log_file.write("\nRestoring Triples. ")
    for dir_, _, files in os.walk(rcs_triples_path):
        for filename in files:
            filepath =  os.path.join(dir_, filename)
            triple_json = get_json_file(filepath)
            triple_obj = triple_collection.one({'_id': ObjectId(triple_json['_id'])})

            if triple_obj:
                log_file.write("\n Found Existing Triple : \n\t " + str(triple_obj))

                if triple_obj._type == "GRelation":
                    if triple_obj.right_subject != triple_json['right_subject']:
                        if type(triple_obj.right_subject) == list:
                            triple_collection.collection.update(
                                {'_id': triple_obj._id},
                                {'$addToSet': {'right_subject': triple_json['right_subject']}},
                                multi=False, upsert=False)
                        else:
                            triple_collection.collection.update(
                                {'_id': triple_obj._id},
                                {'$set': {'right_subject': triple_json['right_subject']}},
                                multi=False, upsert=False)
                        log_file.write("\n GRelation Updated : \n\t OLD: " + str(triple_obj), + "\n\tNew: "+str(triple_json))

                if triple_obj._type == "GAttribute":
                    if triple_obj.object_value != triple_json['object_value']:
                        if type(triple_obj.object_value) == list:
                            triple_collection.collection.update(
                                {'_id': triple_obj._id},
                                {'$addToSet': {'object_value': triple_json['object_value']}},
                                multi=False, upsert=False)
                        else:
                            triple_collection.collection.update(
                                {'_id': triple_obj._id},
                                {'$set': {'object_value': triple_json['object_value']}},
                                multi=False, upsert=False)
                        log_file.write("\n GAttribute Updated: \n\t OLD: " + str(triple_obj), + "\n\tNew: "+str(triple_json))
            else:
                copy_version_file(filepath)
                log_file.write("\n RCS file copied : \n\t" + str(filepath))

                try:
                    log_file.write("\n Inserting Triple doc : " + str(triple_json))
                    node_id = triple_collection.collection.insert(triple_json)
                    triple_obj = triple_collection.one({'_id': node_id})
                    triple_node_RT_AT_id = None
                    if 'attribute_type' in triple_json:
                        triple_node_RT_AT_id = triple_json['attribute_type']
                    else:
                        triple_node_RT_AT_id = triple_json['relation_type']
                    triple_node_RT_AT = node_collection.one({'_id': ObjectId(triple_node_RT_AT_id)})
                    triple_obj.save(triple_node=triple_node_RT_AT, triple_id=triple_node_RT_AT._id)
                    log_file.write("\nUpdate RCS using save()")
                except Exception as tr_insert_err:
                    log_file.write("\nError while inserting Triple obj" + str(tr_insert_err))
                    pass



def restore_counter_objects(rcs_counters_path):
    print "\nRestoring Counters.."
    global log_file
    log_file.write("\nRestoring Counters. ")
    for dir_, _, files in os.walk(rcs_counters_path):
        for filename in files:
            filepath =  os.path.join(dir_, filename)
            counter_json = get_json_file(filepath)
            counter_obj = counter_collection.one({'_id': ObjectId(counter_json['_id'])})
            if counter_obj:
                counter_changed = False
                log_file.write("\nFound Existing Counter Object : " + str(c._id))

                # if counter_obj.last_update != counter_json['last_update'] :
                #     counter_obj.last_update = counter_json['last_update']
                #     counter_changed = True

                if counter_obj.enrolled != counter_json['enrolled'] :
                    counter_obj.enrolled = counter_json['enrolled']
                    counter_changed = True

                if counter_obj.modules_completed != counter_json['modules_completed'] :
                    counter_obj.modules_completed = counter_json['modules_completed']
                    counter_changed = True

                if counter_obj.course_score != counter_json['course_score'] :
                    counter_obj.course_score = counter_json['course_score']
                    counter_changed = True

                if counter_obj.units_completed != counter_json['units_completed'] :
                    counter_obj.units_completed = counter_json['units_completed']
                    counter_changed = True

                if counter_obj.no_comments_by_user != counter_json['no_comments_by_user'] :
                    counter_obj.no_comments_by_user = counter_json['no_comments_by_user']
                    counter_changed = True

                if counter_obj.no_comments_for_user != counter_json['no_comments_for_user'] :
                    counter_obj.no_comments_for_user = counter_json['no_comments_for_user']
                    counter_changed = True

                if counter_obj.no_files_created != counter_json['no_files_created'] :
                    counter_obj.no_files_created = counter_json['no_files_created']
                    counter_changed = True

                if counter_obj.no_visits_gained_on_files != counter_json['no_visits_gained_on_files'] :
                    counter_obj.no_visits_gained_on_files = counter_json['no_visits_gained_on_files']
                    counter_changed = True

                if counter_obj.no_comments_received_on_files != counter_json['no_comments_received_on_files'] :
                    counter_obj.no_comments_received_on_files = counter_json['no_comments_received_on_files']
                    counter_changed = True

                if counter_obj.no_others_files_visited != counter_json['no_others_files_visited'] :
                    counter_obj.no_others_files_visited = counter_json['no_others_files_visited']
                    counter_changed = True

                if counter_obj.no_comments_on_others_files != counter_json['no_comments_on_others_files'] :
                    counter_obj.no_comments_on_others_files = counter_json['no_comments_on_others_files']
                    counter_changed = True

                if counter_obj.rating_count_received_on_files != counter_json['rating_count_received_on_files'] :
                    counter_obj.rating_count_received_on_files = counter_json['rating_count_received_on_files']
                    counter_changed = True

                if counter_obj.avg_rating_received_on_files != counter_json['avg_rating_received_on_files'] :
                    counter_obj.avg_rating_received_on_files = counter_json['avg_rating_received_on_files']
                    counter_changed = True

                if counter_obj.no_questions_attempted != counter_json['no_questions_attempted'] :
                    counter_obj.no_questions_attempted = counter_json['no_questions_attempted']
                    counter_changed = True

                if counter_obj.no_correct_answers != counter_json['no_correct_answers'] :
                    counter_obj.no_correct_answers = counter_json['no_correct_answers']
                    counter_changed = True

                if counter_obj.no_incorrect_answers != counter_json['no_incorrect_answers'] :
                    counter_obj.no_incorrect_answers = counter_json['no_incorrect_answers']
                    counter_changed = True

                if counter_obj.no_notes_written != counter_json['no_notes_written'] :
                    counter_obj.no_notes_written = counter_json['no_notes_written']
                    counter_changed = True

                if counter_obj.no_views_gained_on_notes != counter_json['no_views_gained_on_notes'] :
                    counter_obj.no_views_gained_on_notes = counter_json['no_views_gained_on_notes']
                    counter_changed = True

                if counter_obj.no_others_notes_visited != counter_json['no_others_notes_visited'] :
                    counter_obj.no_others_notes_visited = counter_json['no_others_notes_visited']
                    counter_changed = True

                if counter_obj.no_comments_received_on_notes != counter_json['no_comments_received_on_notes'] :
                    counter_obj.no_comments_received_on_notes = counter_json['no_comments_received_on_notes']
                    counter_changed = True

                if counter_obj.no_comments_on_others_notes != counter_json['no_comments_on_others_notes'] :
                    counter_obj.no_comments_on_others_notes = counter_json['no_comments_on_others_notes']
                    counter_changed = True

                if counter_obj.rating_count_received_on_notes != counter_json['rating_count_received_on_notes'] :
                    counter_obj.rating_count_received_on_notes = counter_json['rating_count_received_on_notes']
                    counter_changed = True

                if counter_obj.avg_rating_received_on_notes != counter_json['avg_rating_received_on_notes'] :
                    counter_obj.avg_rating_received_on_notes = counter_json['avg_rating_received_on_notes']
                    counter_changed = True

                if counter_obj.comments_by_others_on_files != counter_json['comments_by_others_on_files'] and counter_json['comments_by_others_on_files']:
                    n.comments_by_others_on_files.extend(counter_json['comments_by_others_on_files'])
                    counter_changed = True

                if counter_obj.comments_by_others_on_notes != counter_json['comments_by_others_on_notes'] and counter_json['comments_by_others_on_notes']:
                    n.comments_by_others_on_notes.extend(counter_json['comments_by_others_on_notes'])
                    counter_changed = True

                if counter_changed:
                    log_file.write("\n Counter Updated: \n\t OLD: " + str(counter_obj), + "\n\tNew: "+str(counter_json))
                    counter_obj.save()
            else:
                try:
                    log_file.write("\n Inserting Counter doc : " + str(counter_json))
                    node_id = counter_collection.collection.insert(counter_json)
                except Exception as counter_insert_err:
                    log_file.write("\nError while inserting Counter obj" + str(counter_insert_err))
                    pass

def call_group_import(rcs_repo_path):

    rcs_filehives_path = os.path.join(rcs_repo_path, "Filehives")
    rcs_nodes_path = os.path.join(rcs_repo_path, "Nodes")
    rcs_triples_path = os.path.join(rcs_repo_path, "Triples")
    rcs_counters_path = os.path.join(rcs_repo_path, "Counters")

    # Following sequence is IMPORTANT
    restore_filehive_objects(rcs_filehives_path)
    restore_node_objects(rcs_nodes_path)
    restore_triple_objects(rcs_triples_path)
    restore_counter_objects(rcs_counters_path)


def copy_media_data(media_path):
    # MEDIA_ROOT is destination usually: /data/media/
    # media_path is "dump-data/data/media"
    if os.path.exists(media_path):
        media_copy_cmd = "rsync -avzhP " + media_path + "/*  " + MEDIA_ROOT + "/"
        subprocess.Popen(media_copy_cmd,stderr=subprocess.STDOUT,shell=True)
        log_file.write("\n Media Copied:  " + str(media_path) )



class Command(BaseCommand):
    def handle(self, *args, **options):

        global DATA_RESTORE_PATH
        global DATA_DUMP_PATH
        global SCHEMA_ID_MAP
        DATA_RESTORE_PATH = raw_input("\n\tEnter absolute path of data-dump folder to restore:")
        if os.path.exists(DATA_RESTORE_PATH):
            DATA_DUMP_PATH = os.path.join(DATA_RESTORE_PATH, 'dump')
            print "*"*70
            # print "\n Export will be found at: ", DATA_EXPORT_PATH
            print "\n This will take few minutes. Please be patient.\n"
            print "*"*70
            read_config_file()
            
            log_file_path = create_log_file(DATA_RESTORE_PATH)
            global log_file
            log_file.write("\nUpdated CONFIG_VARIABLES: "+ str(CONFIG_VARIABLES))
            print "\n Validating the data-dump"
            validate_data_dump()
            print "\n Checking the dump Group-id availability."
            check_group_availability()
            print "\n User Restoration."
            user_objs_restoration()

            print "\n Factory Schema Restoration. Please wait.."
            SCHEMA_ID_MAP = update_factory_schema_mapper(DATA_DUMP_PATH)
            print "\n Log will be found at: ", log_file_path

            call_group_import(os.path.join(DATA_DUMP_PATH, 'data', 'rcs-repo'))
            copy_media_data(os.path.join(DATA_DUMP_PATH, 'media_files', 'data', 'media'))
        else:
            print "\n No dump found at entered path."
            call_exit()

def parse_datetime_values(d):
    # This decoder will be moved to models next to class NodeJSONEncoder
    if u'uploaded_at' in d:
        d['uploaded_at'] = datetime.datetime.fromtimestamp(d['uploaded_at']/1e3)
    if u'last_update' in d:
        d['last_update'] = datetime.datetime.fromtimestamp(d['last_update']/1e3)
    if u'created_at' in d:
        d['created_at'] = datetime.datetime.fromtimestamp(d['created_at']/1e3)
    if u'attribute_type' in d or u'relation_type' in d:
        d = update_schema_id_for_triple(d)
    if u'attribute_type' in d:
        if d['attribute_type'] in DATE_AT_IDS:
            d['object_value'] = datetime.datetime.fromtimestamp(d['object_value']/1e3)
    if u'attribute_set' in d:
        for each_attr_dict in d['attribute_set']:
            for each_key, each_val in each_attr_dict.iteritems():
                if each_key in ["start_time", "end_time", "start_enroll", "end_enroll"]:
                    each_attr_dict[each_key] = datetime.datetime.fromtimestamp(each_val/1e3)
    return d

def get_json_file(filepath):
    history_manager = HistoryManager()
    rcs = RCS()
    
    # this will create a .json file of the document(node)
    # at manage.py level
    # Returns json and rcs filepath
    try:
        rcs.checkout(filepath)
        fp = filepath.split('/')[-1]
        if fp.endswith(',v'):
            fp = fp.split(',')[0]
        with open(fp, 'r') as version_file:
            obj_as_json = json.loads(version_file.read(), object_hook=json_util.object_hook)
            parse_datetime_values(obj_as_json)
            rcs.checkin(fp)
        # os.remove(fp)
        return obj_as_json
    except Exception as get_json_err:
        print "Exception while getting JSON: ", get_json_err
        pass
