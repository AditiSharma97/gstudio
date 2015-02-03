''' -- imports from python libraries -- '''
# from datetime import datetime
import datetime
import json

''' -- imports from installed packages -- '''
from django.http import HttpResponseRedirect #, HttpResponse uncomment when to use
from django.http import HttpResponse
from django.http import Http404
from django.shortcuts import render_to_response #, render  uncomment when to use
from django.template import RequestContext
from django.template import TemplateDoesNotExist
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.sites.models import Site

from mongokit import IS

from django_mongokit import get_database

try:
  from bson import ObjectId
except ImportError:  # old pymongo
  from pymongo.objectid import ObjectId

''' -- imports from application folders/files -- '''
from gnowsys_ndf.settings import GAPPS, MEDIA_ROOT, GSTUDIO_TASK_TYPES
from gnowsys_ndf.ndf.models import Node, AttributeType, RelationType
from gnowsys_ndf.ndf.views.file import save_file
from gnowsys_ndf.ndf.views.methods import get_node_common_fields, parse_template_data
from gnowsys_ndf.ndf.views.notify import set_notif_val
from gnowsys_ndf.ndf.views.methods import get_property_order_with_value
from gnowsys_ndf.ndf.views.methods import create_gattribute, create_grelation, create_task

collection = get_database()[Node.collection_name]
app = collection.Node.one({'_type': "GSystemType", 'name': GAPPS[7]})


@login_required
def enrollment_create_edit(request, group_id, app_id, app_set_id=None, app_set_instance_id=None, app_name=None):
    """
    Creates/Modifies document of given sub-types of Course(s).
    """

    auth = None
    if ObjectId.is_valid(group_id) is False:
        group_ins = collection.Node.one({'_type': "Group", "name": group_id})
        auth = collection.Node.one(
            {'_type': 'Author', 'name': unicode(request.user.username)}
        )
        if group_ins:
            group_id = str(group_ins._id)
        else:
            auth = collection.Node.one(
                {'_type': 'Author', 'name': unicode(request.user.username)}
            )
            if auth:
                group_id = str(auth._id)

    app = None
    if app_id is None:
        app = collection.Node.one({'_type': "GSystemType", 'name': app_name})
        if app:
            app_id = str(app._id)
    else:
        app = collection.Node.one({'_id': ObjectId(app_id)})

    app_name = app.name

    app_collection_set = []
    title = ""

    enrollment_gst = None
    enrollment_gs = None
    mis_admin = None
    college_group_id = None
    latest_completed_on = None
    lock_start_enroll = False # Will only be True while editing (i.e. Re-opening Enrollment)
    reopen_task_id = None

    property_order_list = []

    template = ""
    template_prefix = "mis"

    user_id = request.user.id

    if request.user:
        if auth is None:
            auth = collection.Node.one(
                {'_type': 'Author', 'name': unicode(request.user.username)}
            )
        agency_type = auth.agency_type
        agency_type_node = collection.Node.one(
            {'_type': "GSystemType", 'name': agency_type}, {'collection_set': 1}
        )
        if agency_type_node:
            for eachset in agency_type_node.collection_set:
                app_collection_set.append(
                    collection.Node.one(
                        {"_id": eachset}, {'_id': 1, 'name': 1, 'type_of': 1}
                    )
                )

    if app_set_id:
        enrollment_gst = collection.Node.one(
            {'_type': "GSystemType", '_id': ObjectId(app_set_id)},
            {'name': 1, 'type_of': 1}
        )
        template = "ndf/" \
            + enrollment_gst.name.strip().lower().replace(' ', '_') \
            + "_create_edit.html"

        title = enrollment_gst.name
        enrollment_gs = collection.GSystem()
        enrollment_gs.member_of.append(enrollment_gst._id)

    if app_set_instance_id:
        enrollment_gs = collection.Node.one({
            '_type': "GSystem", '_id': ObjectId(app_set_instance_id)
        })

        for attr in enrollment_gs.attribute_set:
            if attr and "has_enrollment_task" in attr:
                td = attr["has_enrollment_task"]
                latest_completed_on = None # Must hold latest completed_on
                for k, completed_by_on in td.items():
                    if latest_completed_on:
                        if "completed_on" in completed_by_on and latest_completed_on < completed_by_on["completed_on"]:
                            # Put latest changed date
                            latest_completed_on = completed_by_on["completed_on"]
                    else:
                        if "completed_on" in completed_by_on and completed_by_on["completed_on"]:
                            latest_completed_on = completed_by_on["completed_on"]

    property_order_list = get_property_order_with_value(enrollment_gs)

    if request.method == "POST":
        start_enroll = ""
        if "start_enroll" in request.POST:
            start_enroll = request.POST.get("start_enroll", "")
            start_enroll = datetime.datetime.strptime(start_enroll, "%d/%m/%Y")

        end_enroll = ""
        if "end_enroll" in request.POST:
            end_enroll = request.POST.get("end_enroll", "")
            end_enroll = datetime.datetime.strptime(end_enroll, "%d/%m/%Y")

        nussd_course_type = ""
        if "nussd_course_type" in request.POST:
            nussd_course_type = request.POST.get("nussd_course_type", "")
            nussd_course_type = unicode(nussd_course_type)

        # Only exists while updating StudentCourseEnrollment node's duration
        if "reopen_task_id" in request.POST:
            reopen_task_id = request.POST.get("reopen_task_id", "")
            reopen_task_id = ObjectId(reopen_task_id)

        if latest_completed_on:
            if latest_completed_on < end_enroll:
                lock_start_enroll = True

        at_rt_list = ["start_enroll", "end_enroll", "for_acourse", "for_college", "for_university", "enrollment_status", "has_enrolled", "has_enrollment_task", "has_approval_task"]
        at_rt_dict = {}

        ac_cname_cl_uv_ids = []
        ann_course_ids = None
        course_name = None
        college_id = None
        university_id = None

        college_po = {}
        mis_admin = collection.Node.one(
            {'_type': "Group", 'name': "MIS_admin"}, {'name': 1}
        )

        if "ac_cname_cl_uv_ids" in request.POST:
            ac_cname_cl_uv_ids = request.POST.get("ac_cname_cl_uv_ids", "")
            ac_cname_cl_uv_ids = json.loads(ac_cname_cl_uv_ids)

        else:
            if enrollment_gs:
                ac_cname_cl_uv_ids = []

                for rel in enrollment_gs.relation_set:
                    if rel and "for_acourse" in rel:
                        ann_course_ids_set = rel["for_acourse"]

                if len(ann_course_ids_set) > 1 or "Foundation_Course" in enrollment_gs.name:
                    # Foundation
                    ann_course_ids = ann_course_ids_set

                    ann_course_node = collection.Node.one({
                        "_id": ObjectId(ann_course_ids[0])
                    })

                    ann_course_node.get_neighbourhood(ann_course_node.member_of)

                    # Ann course name
                    ann_course_name = ann_course_node.name

                    start_time_ac = ann_course_node.start_time.strftime("%Y")
                    end_time_ac = ann_course_node.end_time.strftime("%Y")

                    for each_attr in ann_course_node.announced_for[0].attribute_set:
                        if each_attr and "nussd_course_type" in each_attr:
                            nussd_course_type = each_attr["nussd_course_type"]
                            break

                    # College
                    college_id = ann_course_node.acourse_for_college[0]["_id"]

                    # University
                    for colg_rel in ann_course_node.acourse_for_college[0].relation_set:
                        if colg_rel and "college_affiliated_to" in colg_rel:
                            university_id = colg_rel["college_affiliated_to"][0]
                            break

                    colg_code = ""
                    for colg_attr in ann_course_node.acourse_for_college[0].attribute_set:
                        if colg_attr and "enrollment_code" in colg_attr:
                            colg_code = colg_attr["enrollment_code"]
                            break

                    # course name
                    course_name = "Foundation_Course_" + str(colg_code) + "_" + str(start_time_ac) + "_" + str(end_time_ac)

                    ac_cname_cl_uv_ids.append([ann_course_ids, ann_course_name, course_name, college_id, university_id])

                else:
                    # Domain
                    for each_ac in ann_course_ids_set:
                        ann_course_ids = [each_ac]

                        ann_course_node = collection.Node.one({
                            "_id": ObjectId(each_ac)
                        })

                        ann_course_node.get_neighbourhood(ann_course_node.member_of)

                        # Ann course name
                        ann_course_name = ann_course_node.name

                        # course name
                        course_name = ann_course_node.announced_for[0].name

                        for each_attr in ann_course_node.announced_for[0].attribute_set:
                            if each_attr and "nussd_course_type" in each_attr:
                                nussd_course_type = each_attr["nussd_course_type"]
                                break

                        # College
                        college_id = ann_course_node.acourse_for_college[0]["_id"]

                        # University
                        for colg_rel in ann_course_node.acourse_for_college[0].relation_set:
                            if colg_rel and "college_affiliated_to" in colg_rel:
                                university_id = colg_rel["college_affiliated_to"][0]
                                break

                        ac_cname_cl_uv_ids.append([ann_course_ids, ann_course_name, course_name, college_id, university_id])

        if nussd_course_type == "Foundation Course":
            for each_fc_set in ac_cname_cl_uv_ids:
                fc_set = each_fc_set[0]
                ann_course_name = each_fc_set[1]
                course_name = each_fc_set[2]
                college_id = each_fc_set[3]
                university_id = each_fc_set[4]

                fc_set = [ObjectId(each_fc) for each_fc in each_fc_set[0]]
                at_rt_dict["for_acourse"] = fc_set
                acourse_id = fc_set
                at_rt_dict["for_acourse"] = acourse_id
                at_rt_dict["enrollment_status"] = u"OPEN"
                at_rt_dict["start_enroll"] = start_enroll
                at_rt_dict["end_enroll"] = end_enroll
                at_rt_dict["for_college"] = college_id

                task_group_set = []
                if college_id not in college_po:
                    college_node = collection.Node.one({
                        "_id": ObjectId(college_id)
                    }, {
                        "name": 1,
                        "relation_set.has_group": 1,
                        "relation_set.has_officer_incharge": 1
                    })

                    for rel in college_node.relation_set:
                        if rel and "has_officer_incharge" in rel:
                            college_po[college_id] = rel["has_officer_incharge"]
                        if rel and "has_group" in rel:
                            college_group_id = rel["has_group"][0]
                            task_group_set.append(college_group_id)

                at_rt_dict["for_university"] = ObjectId(university_id)
                enrollment_gst = collection.Node.one({
                    '_type': "GSystemType", 'name': "StudentCourseEnrollment"
                })

                enrollment_gs_name = "StudentCourseEnrollment" \
                    + "_" + ann_course_name
                enrollment_gs = collection.Node.one({
                    'member_of': enrollment_gst._id, 'name': enrollment_gs_name,
                    "group_set": [mis_admin._id, college_group_id],
                    'status': u"PUBLISHED"
                })

                # If not found, create it
                if not enrollment_gs:
                    enrollment_gs = collection.GSystem()
                    enrollment_gs.name = enrollment_gs_name
                    if enrollment_gst._id not in enrollment_gs.member_of:
                        enrollment_gs.member_of.append(enrollment_gst._id)

                    if mis_admin._id not in enrollment_gs.group_set:
                        enrollment_gs.group_set.append(mis_admin._id)
                    if college_group_id not in enrollment_gs.group_set:
                        enrollment_gs.group_set.append(college_group_id)

                    user_id = request.user.id
                    enrollment_gs.created_by = user_id
                    enrollment_gs.modified_by = user_id
                    if user_id not in enrollment_gs.contributors:
                        enrollment_gs.contributors.append(user_id)

                    enrollment_gs.last_update = datetime.datetime.today()
                    enrollment_gs.status = u"PUBLISHED"
                    enrollment_gs.save()

                if "_id" in enrollment_gs:
                    # [2] Create task for PO of respective college
                    # for Student-Course Enrollment
                    task_dict = {}
                    task_name = "StudentCourseEnrollment_Task" + "_" + \
                        start_enroll.strftime("%d-%b-%Y") + "_" + end_enroll.strftime("%d-%b-%Y") + \
                        "_" + course_name
                    task_name = unicode(task_name)
                    task_dict["name"] = task_name
                    task_dict["created_by"] = request.user.id
                    task_dict["created_by_name"] = request.user.username
                    task_dict["modified_by"] = request.user.id
                    task_dict["contributors"] = [request.user.id]

                    task_node = None

                    task_dict["start_time"] = start_enroll
                    task_dict["end_time"] = end_enroll

                    glist_gst = collection.Node.one({'_type': "GSystemType", 'name': "GList"})
                    task_type_node = None
                    # Here, GSTUDIO_TASK_TYPES[3] := 'Student-Course Enrollment'
                    task_dict["has_type"] = []
                    if glist_gst:
                        task_type_node = collection.Node.one(
                            {'member_of': glist_gst._id, 'name': GSTUDIO_TASK_TYPES[3]},
                            {'_id': 1}
                        )

                        if task_type_node:
                            task_dict["has_type"].append(task_type_node._id)

                    task_dict["Status"] = u"New"
                    task_dict["Priority"] = u"High"

                    task_dict["content_org"] = u""

                    task_dict["Assignee"] = []
                    task_dict["group_set"] = []

                    # From Program Officer node(s) assigned to college using college_po[college_id]
                    # From each node's 'has_login' relation fetch corresponding Author node
                    po_cur = collection.Node.find({
                        '_id': {'$in': college_po[college_id]},
                        'attribute_set.email_id': {'$exists': True},
                        'relation_set.has_login': {'$exists': True}
                    }, {
                        'name': 1, 'attribute_set.email_id': 1,
                        'relation_set.has_login': 1
                    })
                    for PO in po_cur:
                        po_auth = None
                        for rel in PO.relation_set:
                            if rel and "has_login" in rel:
                                po_auth = collection.Node.one({'_type': "Author", '_id': ObjectId(rel["has_login"][0])})
                                if po_auth:
                                    if po_auth.created_by not in task_dict["Assignee"]:
                                        task_dict["Assignee"].append(po_auth.created_by)
                                    if po_auth._id not in task_dict["group_set"]:
                                        task_dict["group_set"].append(po_auth._id)

                    # Appending college group's ObjectId to group_set
                    task_dict["group_set"].extend(task_group_set)

                    task_node = create_task(task_dict)

                    MIS_GAPP = collection.Node.one({
                        "_type": "GSystemType", "name": "MIS"
                    })

                    Student = collection.Node.one({
                        "_type": "GSystemType", "name": "Student"
                    })

                    # Set content_org for the task with link having ObjectId of it's own
                    if MIS_GAPP and Student:
                        site = Site.objects.get(pk=1)
                        site = site.name.__str__()
                        college_enrollment_url_link = "http://" + site + "/" + \
                            college_node.name.replace(" ", "%20").encode('utf8') + \
                            "/mis/" + str(MIS_GAPP._id) + "/" + str(enrollment_gst._id) + "/enroll" + \
                            "/" + str(enrollment_gs._id) + \
                            "?task_id=" + str(task_node._id) + "&nussd_course_type=" + \
                            nussd_course_type

                        task_dict = {}
                        task_dict["_id"] = task_node._id
                        task_dict["name"] = task_name
                        task_dict["created_by_name"] = request.user.username
                        task_dict["content_org"] = "\n- Please click [[" + college_enrollment_url_link + "][here]] to enroll students in " + \
                            ann_course_name + " course." + "\n\n- This enrollment procedure is open for duration between " + \
                            start_enroll.strftime("%d-%b-%Y") + " and " + end_enroll.strftime("%d-%b-%Y") + "."

                        task_node = create_task(task_dict)

                    enrollment_task_dict = {}
                    for each_enrollment in enrollment_gs.attribute_set:
                        if "has_enrollment_task" in each_enrollment:
                            if each_enrollment["has_enrollment_task"]:
                                enrollment_task_dict = each_enrollment["has_enrollment_task"]
                                break

                    if str(task_node._id) not in enrollment_task_dict:
                        enrollment_task_dict[str(task_node._id)] = {}

                    at_rt_dict["has_enrollment_task"] = enrollment_task_dict
                    # Save/Update GAttribute(s) and/or GRelation(s)
                    for at_rt_name in at_rt_list:
                        if at_rt_name in at_rt_dict:
                            at_rt_type_node = collection.Node.one({
                                '_type': {'$in': ["AttributeType", "RelationType"]},
                                'name': at_rt_name
                            })

                            if at_rt_type_node:
                                at_rt_node = None

                                if at_rt_type_node._type == "AttributeType" and at_rt_dict[at_rt_name]:
                                    at_rt_node = create_gattribute(enrollment_gs._id, at_rt_type_node, at_rt_dict[at_rt_name])

                                elif at_rt_type_node._type == "RelationType" and at_rt_dict[at_rt_name]:
                                    at_rt_node = create_grelation(enrollment_gs._id, at_rt_type_node, at_rt_dict[at_rt_name])

        else:
            for each_set in ac_cname_cl_uv_ids:
                acourse_id = ObjectId(each_set[0][0])
                at_rt_dict["for_acourse"] = acourse_id
                at_rt_dict["enrollment_status"] = u"OPEN"
                at_rt_dict["start_enroll"] = start_enroll
                at_rt_dict["end_enroll"] = end_enroll
                ann_course_name = each_set[1]
                course_name = each_set[2]

                college_id = ObjectId(each_set[3])
                at_rt_dict["for_college"] = college_id

                task_group_set = []
                if college_id not in college_po:
                    college_node = collection.Node.one({
                        "_id": college_id
                    }, {
                        "name": 1,
                        "relation_set.has_group": 1,
                        "relation_set.has_officer_incharge": 1
                    })

                    for rel in college_node.relation_set:
                        if rel and "has_officer_incharge" in rel:
                            college_po[college_id] = rel["has_officer_incharge"]
                        if rel and "has_group" in rel:
                            college_group_id = rel["has_group"][0]
                            task_group_set.append(college_group_id)

                at_rt_dict["for_university"] = ObjectId(each_set[4])
                enrollment_gst = collection.Node.one({
                    '_type': "GSystemType", 'name': "StudentCourseEnrollment"
                })

                enrollment_gs_name = "StudentCourseEnrollment" \
                    + "_" + ann_course_name
                enrollment_gs = collection.Node.one({
                    'member_of': enrollment_gst._id, 'name': enrollment_gs_name,
                    "group_set": [mis_admin._id, college_group_id],
                    'status': u"PUBLISHED"
                })

                # If not found, create it
                if not enrollment_gs:
                    enrollment_gs = collection.GSystem()
                    enrollment_gs.name = enrollment_gs_name
                    if enrollment_gst._id not in enrollment_gs.member_of:
                        enrollment_gs.member_of.append(enrollment_gst._id)

                    if mis_admin._id not in enrollment_gs.group_set:
                        enrollment_gs.group_set.append(mis_admin._id)
                    if college_group_id not in enrollment_gs.group_set:
                        enrollment_gs.group_set.append(college_group_id)

                    enrollment_gs.created_by = user_id
                    enrollment_gs.modified_by = user_id
                    if user_id not in enrollment_gs.contributors:
                        enrollment_gs.contributors.append(user_id)

                    enrollment_gs.last_update = datetime.datetime.today()
                    enrollment_gs.status = u"PUBLISHED"
                    enrollment_gs.save()

                if "_id" in enrollment_gs:
                    # [2] Create task for PO of respective college
                    # for Student-Course Enrollment
                    task_dict = {}
                    task_name = "StudentCourseEnrollment_Task" + "_" + \
                        start_enroll.strftime("%d-%b-%Y") + "_" + end_enroll.strftime("%d-%b-%Y") + \
                        "_" + ann_course_name
                    task_name = unicode(task_name)
                    task_dict["name"] = task_name
                    task_dict["created_by"] = request.user.id
                    task_dict["created_by_name"] = request.user.username
                    task_dict["modified_by"] = request.user.id
                    task_dict["contributors"] = [request.user.id]

                    task_node = None

                    task_dict["start_time"] = start_enroll
                    task_dict["end_time"] = end_enroll

                    glist_gst = collection.Node.one({'_type': "GSystemType", 'name': "GList"})
                    task_type_node = None
                    # Here, GSTUDIO_TASK_TYPES[3] := 'Student-Course Enrollment'
                    task_dict["has_type"] = []
                    if glist_gst:
                        task_type_node = collection.Node.one(
                            {'member_of': glist_gst._id, 'name': GSTUDIO_TASK_TYPES[3]},
                            {'_id': 1}
                        )

                        if task_type_node:
                            task_dict["has_type"].append(task_type_node._id)

                    task_dict["Status"] = u"New"
                    task_dict["Priority"] = u"High"

                    task_dict["content_org"] = u""

                    task_dict["Assignee"] = []
                    task_dict["group_set"] = []

                    # From Program Officer node(s) assigned to college using college_po[college_id]
                    # From each node's 'has_login' relation fetch corresponding Author node
                    po_cur = collection.Node.find({
                        '_id': {'$in': college_po[college_id]},
                        'attribute_set.email_id': {'$exists': True},
                        'relation_set.has_login': {'$exists': True}
                    }, {
                        'name': 1, 'attribute_set.email_id': 1,
                        'relation_set.has_login': 1
                    })
                    for PO in po_cur:
                        po_auth = None
                        for rel in PO.relation_set:
                            if rel and "has_login" in rel:
                                po_auth = collection.Node.one({'_type': "Author", '_id': ObjectId(rel["has_login"][0])})
                                if po_auth:
                                    if po_auth.created_by not in task_dict["Assignee"]:
                                        task_dict["Assignee"].append(po_auth.created_by)
                                    if po_auth._id not in task_dict["group_set"]:
                                        task_dict["group_set"].append(po_auth._id)

                    # Appending college group's ObjectId to group_set
                    task_dict["group_set"].extend(task_group_set)

                    task_node = create_task(task_dict)

                    MIS_GAPP = collection.Node.one({
                        "_type": "GSystemType", "name": "MIS"
                    })

                    Student = collection.Node.one({
                        "_type": "GSystemType", "name": "Student"
                    })

                    # Set content_org for the task with link having ObjectId of it's own
                    if MIS_GAPP and Student:
                        site = Site.objects.get(pk=1)
                        site = site.name.__str__()
                        college_enrollment_url_link = "http://" + site + "/" + \
                            college_node.name.replace(" ", "%20").encode('utf8') + \
                            "/mis/" + str(MIS_GAPP._id) + "/" + str(enrollment_gst._id) + "/enroll" + \
                            "/" + str(enrollment_gs._id) + \
                            "?task_id=" + str(task_node._id) + "&nussd_course_type=" + \
                            nussd_course_type + "&ann_course_id=" + str(acourse_id)

                        task_dict = {}
                        task_dict["_id"] = task_node._id
                        task_dict["name"] = task_name
                        task_dict["created_by_name"] = request.user.username
                        task_dict["content_org"] = "\n- Please click [[" + college_enrollment_url_link + "][here]] to enroll students in " + \
                            ann_course_name + " course." + "\n\n- This enrollment procedure is open for duration between " + \
                            start_enroll.strftime("%d-%b-%Y") + " and " + end_enroll.strftime("%d-%b-%Y") + "."

                        task_node = create_task(task_dict)

                    enrollment_task_dict = {}
                    for each_enrollment in enrollment_gs.attribute_set:
                        if "has_enrollment_task" in each_enrollment:
                            if each_enrollment["has_enrollment_task"]:
                                enrollment_task_dict = each_enrollment["has_enrollment_task"]
                                break

                    if str(task_node._id) not in enrollment_task_dict:
                        enrollment_task_dict[str(task_node._id)] = {}

                    at_rt_dict["has_enrollment_task"] = enrollment_task_dict
                    # Save/Update GAttribute(s) and/or GRelation(s)
                    for at_rt_name in at_rt_list:
                        if at_rt_name in at_rt_dict:
                            at_rt_type_node = collection.Node.one({
                                '_type': {'$in': ["AttributeType", "RelationType"]},
                                'name': at_rt_name
                            })

                            if at_rt_type_node:
                                at_rt_node = None

                                if at_rt_type_node._type == "AttributeType" and at_rt_dict[at_rt_name]:
                                    at_rt_node = create_gattribute(enrollment_gs._id, at_rt_type_node, at_rt_dict[at_rt_name])

                                elif at_rt_type_node._type == "RelationType" and at_rt_dict[at_rt_name]:
                                    at_rt_node = create_grelation(enrollment_gs._id, at_rt_type_node, at_rt_dict[at_rt_name])

        if reopen_task_id:
            # Update the Re-open enrollment task as "Closed"
            task_dict["_id"] = reopen_task_id
            task_dict["Status"] = u"Closed"
            task_dict["modified_by"] = user_id
            task_node = create_task(task_dict)

        return HttpResponseRedirect(reverse(
            app_name.lower() + ":" + template_prefix + '_app_detail',
            kwargs={'group_id': group_id, "app_id": app_id, "app_set_id": app_set_id}
        ))

    else:
        # GET request
        if "reopen_task_id" in request.GET:
            reopen_task_id = request.GET.get("reopen_task_id", "")
            reopen_task_id = ObjectId(reopen_task_id)

    default_template = "ndf/enrollment_create_edit.html"
    context_variables = {
        'groupid': group_id, 'group_id': group_id,
        'app_id': app_id, 'app_name': app_name,
        'app_collection_set': app_collection_set,
        'app_set_id': app_set_id,
        'title': title,
        'property_order_list': property_order_list
    }

    if app_set_instance_id:
        enrollment_gs.get_neighbourhood(enrollment_gs.member_of)
        context_variables['node'] = enrollment_gs
        context_variables['reopen_task_id'] = reopen_task_id
        for each_in in enrollment_gs.attribute_set:
            for eachk, eachv in each_in.items():
                context_variables[eachk] = eachv

        for each_in in enrollment_gs.relation_set:
            for eachk, eachv in each_in.items():
                get_node_name = collection.Node.one({'_id': eachv[0]})
                context_variables[eachk] = get_node_name.name

    try:
        return render_to_response(
            [template, default_template],
            context_variables,
            context_instance=RequestContext(request)
        )

    except TemplateDoesNotExist as tde:
        error_message = "\n EnrollmentCreateEditViewError: This html template (" \
            + str(tde) + ") does not exists !!!\n"
        raise Http404(error_message)

    except Exception as e:
        error_message = "\n EnrollmentCreateEditViewError: " + str(e) + " !!!\n"
        raise Exception(error_message)


@login_required
def enrollment_detail(request, group_id, app_id, app_set_id=None, app_set_instance_id=None, app_name=None):
  """
  custom view for custom GAPPS
  """

  auth = None
  if ObjectId.is_valid(group_id) is False :
    group_ins = collection.Node.one({'_type': "Group","name": group_id})
    auth = collection.Node.one({'_type': 'Author', 'name': unicode(request.user.username) })
    if group_ins:
      group_id = str(group_ins._id)
    else :
      auth = collection.Node.one({'_type': 'Author', 'name': unicode(request.user.username) })
      if auth :
        group_id = str(auth._id)
  else :
    pass

  app = None
  if app_id is None:
    app = collection.Node.one({'_type': "GSystemType", 'name': app_name})
    if app:
      app_id = str(app._id)
  else:
    app = collection.Node.one({'_id': ObjectId(app_id)})

  app_name = app.name 

  # app_name = "mis"
  app_set = ""
  app_collection_set = []
  title = ""

  sce_gst = None
  sce_gs = None

  nodes = None
  nodes_keys = []
  node = None
  property_order_list = []
  widget_for = []
  is_link_needed = True         # This is required to show Link button on interface that link's Student's/VoluntaryTeacher's node with it's corresponding Author node

  template_prefix = "mis"
  context_variables = {}

  if request.user:
    if auth is None:
      auth = collection.Node.one({'_type': 'Author', 'name': unicode(request.user.username)})
    agency_type = auth.agency_type
    agency_type_node = collection.Node.one({'_type': "GSystemType", 'name': agency_type}, {'collection_set': 1})
    if agency_type_node:
      for eachset in agency_type_node.collection_set:
        app_collection_set.append(collection.Node.one({"_id": eachset}, {'_id': 1, 'name': 1, 'type_of': 1}))      

  if app_set_id:
    sce_gst = collection.Node.one({'_type': "GSystemType", '_id': ObjectId(app_set_id)})#, {'name': 1, 'type_of': 1})
    title = sce_gst.name

    query = {}
    if request.method == "POST":
      search = request.POST.get("search","")
      query = {'member_of': sce_gst._id, 'group_set': ObjectId(group_id), 'name': {'$regex': search, '$options': 'i'}}

    else:
      query = {'member_of': sce_gst._id, 'group_set': ObjectId(group_id)}

    nodes = list(collection.Node.find(query).sort('name', 1))

    nodes_keys = [('name', "Name")]
    template = ""
    template = "ndf/" + sce_gst.name.strip().lower().replace(' ', '_') + "_list.html"
    default_template = "ndf/mis_list.html"

  if app_set_instance_id:
    template = "ndf/" + sce_gst.name.strip().lower().replace(' ', '_') + "_details.html"
    default_template = "ndf/mis_details.html"

    node = collection.Node.one({'_type': "GSystem", '_id': ObjectId(app_set_instance_id)})
    property_order_list = get_property_order_with_value(node)
    node.get_neighbourhood(node.member_of)

  context_variables = { 'groupid': group_id, 
                        'app_id': app_id, 'app_name': app_name, 'app_collection_set': app_collection_set, 
                        'app_set_id': app_set_id,
                        'title': title,
                        'nodes': nodes, "nodes_keys": nodes_keys, 'node': node,
                        'property_order_list': property_order_list, 'lstFilters': widget_for,
                        'is_link_needed': is_link_needed
                      }
  try:
    return render_to_response([template, default_template], 
                              context_variables,
                              context_instance = RequestContext(request)
                            )
  
  except TemplateDoesNotExist as tde:
    error_message = "\n StudentCourseEnrollmentDetailListViewError: This html template (" + str(tde) + ") does not exists !!!\n"
    raise Http404(error_message)
  
  except Exception as e:
    error_message = "\n StudentCourseEnrollmentDetailListViewError: " + str(e) + " !!!\n"
    raise Exception(error_message)


@login_required
def enrollment_enroll(request, group_id, app_id, app_set_id=None, app_set_instance_id=None, app_name=None):
    """
    Student enrollment
    """
    auth = None
    if ObjectId.is_valid(group_id) is False:
        group_ins = collection.Node.one({'_type': "Group", "name": group_id})
        auth = collection.Node.one({'_type': 'Author', 'name': unicode(request.user.username) })
        if group_ins:
            group_id = str(group_ins._id)
        else:
            auth = collection.Node.one({'_type': 'Author', 'name': unicode(request.user.username) })
            if auth:
                group_id = str(auth._id)

    app = None
    if app_id is None:
        app = collection.Node.one({'_type': "GSystemType", 'name': app_name})
        if app:
            app_id = str(app._id)
    else:
        app = collection.Node.one({'_id': ObjectId(app_id)})

    app_name = app.name

    app_collection_set = []
    # app_set = ""
    # nodes = ""
    title = ""
    template_prefix = "mis"

    user_id = int(request.user.id)  # getting django user id
    # user_name = unicode(request.user.username)  # getting django user name

    if user_id:
        if auth is None:
            auth = collection.Node.one({
                '_type': 'Author', 'name': unicode(request.user.username)
            })

        agency_type = auth.agency_type
        agency_type_node = collection.Node.one({
            '_type': "GSystemType", 'name': agency_type
        }, {
            'collection_set': 1
        })
        if agency_type_node:
            for eachset in agency_type_node.collection_set:
                app_collection_set.append(collection.Node.one({
                    "_id": eachset
                }, {
                    '_id': 1, 'name': 1, 'type_of': 1
                }))

    sce_gs = None
    sce_last_update = None
    ann_course_list = []
    ann_course_name = ""
    nussd_course_type = ""
    start_time = ""
    end_time = ""
    start_enroll = ""
    end_enroll = ""
    enrollment_closed = False
    enrollment_reopen = False
    total_student_enroll_list = []
    student_enroll_list = []
    college_enrollment_code = ""
    task_dict = {}
    task_id = None
    at_rt_dict = {}
    req_ats = []

    if app_set_instance_id:
        if ObjectId.is_valid(app_set_instance_id):
            sce_gs = collection.Node.one({
                '_id': ObjectId(app_set_instance_id)
            })

            sce_gs.get_neighbourhood(sce_gs.member_of)

            for task_objectid, task_details_dict in sce_gs.has_enrollment_task.items():
                if not task_details_dict:
                    task_id = ObjectId(task_objectid)

            for each in sce_gs.for_acourse:
                ann_course_list.append([str(each._id), each.name])
                ann_course_name = each.name

            start_enroll = sce_gs.start_enroll
            end_enroll = sce_gs.end_enroll
            if sce_gs.enrollment_status in [u"APPROVAL", u"CLOSED"]:
                enrollment_closed = True
            elif sce_gs.enrollment_status in u"PENDING":
                enrollment_reopen = True
            total_student_enroll_list = sce_gs.has_enrolled

            for attr in sce_gs.for_acourse[0].attribute_set:
                if attr and "start_time" in attr:
                    start_time = attr["start_time"]
                if attr and "end_time" in attr:
                    end_time = attr["end_time"]
                if attr and "nussd_course_type" in attr:
                    nussd_course_type = attr["nussd_course_type"]

            for attr in sce_gs.for_college[0].attribute_set:
                if attr and "enrollment_code" in attr:
                    college_enrollment_code = attr["enrollment_code"]
                    break

    if request.method == "POST":
        enroll_state = request.POST.get("enrollState", "")
        at_rt_list = ["start_enroll", "end_enroll", "for_acourse", "for_college", "for_university", "enrollment_status", "has_enrolled", "has_enrollment_task", "has_approval_task", "has_current_approval_task"]

        mis_admin = collection.Node.one({
            '_type': "Group", 'name': "MIS_admin"
        })
        if enroll_state == "Re-open Enrollment":
            task_dict["name"] = ""
            if nussd_course_type == "Foundation Course":
                task_dict["name"] = "StudentCourseReOpenEnrollment_Task" + "_" + \
                    start_enroll.strftime("%d-%b-%Y") + "_" + end_enroll.strftime("%d-%b-%Y") + \
                    "_FC_" + college_enrollment_code + "_" + start_time.strftime("%b-%Y") + "_" + end_time.strftime("%b-%Y")

            else:
                task_dict["name"] = "StudentCourseReOpenEnrollment_Task" + "_" + \
                    start_enroll.strftime("%d-%b-%Y") + "_" + end_enroll.strftime("%d-%b-%Y") + \
                    "_" + ann_course_name

            task_dict["name"] = unicode(task_dict["name"])
            task_dict["created_by"] = mis_admin.group_admin[0]
            admin_user = User.objects.get(id=mis_admin.group_admin[0])
            task_dict["created_by_name"] = admin_user.username
            task_dict["modified_by"] = mis_admin.group_admin[0]
            task_dict["contributors"] = [mis_admin.group_admin[0]]

            MIS_GAPP = collection.Node.one({
                '_type': "GSystemType", 'name': "MIS"
            }, {
                '_id': 1
            })

            sce_gst = collection.Node.one({
                "_type": "GSystemType", "name": "StudentCourseEnrollment"
            })

            task_dict["start_time"] = datetime.datetime.now()
            task_dict["end_time"] = None

            glist_gst = collection.Node.one({'_type': "GSystemType", 'name': "GList"})
            task_type_node = None
            # Here, GSTUDIO_TASK_TYPES[7] := 'Re-open Student-Course Enrollment'
            task_dict["has_type"] = []
            if glist_gst:
                task_type_node = collection.Node.one({
                    'member_of': glist_gst._id, 'name': GSTUDIO_TASK_TYPES[7]
                }, {
                    '_id': 1
                })

                if task_type_node:
                    task_dict["has_type"].append(task_type_node._id)

            task_dict["Status"] = u"New"
            task_dict["Priority"] = u"High"
            task_dict["content_org"] = u""

            task_dict["group_set"] = [mis_admin._id]

            task_dict["Assignee"] = []
            for each_admin_id in mis_admin.group_admin:
                task_dict["Assignee"].append(each_admin_id)

            task_node = create_task(task_dict)

            # Set content for Re-open task (having it's own ObjectId)
            task_dict = {}
            task_dict["_id"] = task_node._id
            task_dict["name"] = task_node.name
            task_dict["created_by_name"] = admin_user.username
            student_course_reopen_enrollment_url_link = ""
            site = Site.objects.get(pk=1)
            site = site.name.__str__()
            student_course_reopen_enrollment_url_link = "http://" + site + "/" + \
                mis_admin.name.replace(" ", "%20").encode('utf8') + \
                "/mis/" + str(MIS_GAPP._id) + "/" + str(sce_gst._id) + "/edit" + \
                "/" + str(sce_gs._id) + "?reopen_task_id=" + str(task_node._id)

            task_dict["content_org"] = "\n- Please click [[" + \
                student_course_reopen_enrollment_url_link + "][here]] to re-open enrollment."

            task_dict["content_org"] = unicode(task_dict["content_org"])
            task_node = create_task(task_dict)

            # Update StudentCourseEnrollment node's enrollment_status to "PENDING"
            # PENDING means in a state where admin should reset enrollment duration
            at_rt_dict["enrollment_status"] = u"PENDING"

            for at_rt_name in at_rt_list:
                if at_rt_name in at_rt_dict:
                    at_rt_type_node = collection.Node.one({
                        '_type': {'$in': ["AttributeType", "RelationType"]},
                        'name': at_rt_name
                    })

                    if at_rt_type_node:
                        at_rt_node = None

                        if at_rt_type_node._type == "AttributeType" and at_rt_dict[at_rt_name]:
                            at_rt_node = create_gattribute(sce_gs._id, at_rt_type_node, at_rt_dict[at_rt_name])

                        elif at_rt_type_node._type == "RelationType" and at_rt_dict[at_rt_name]:
                            at_rt_node = create_grelation(sce_gs._id, at_rt_type_node, at_rt_dict[at_rt_name])

            return HttpResponseRedirect(reverse(app_name.lower() + ":" + template_prefix + '_enroll',
                kwargs={'group_id': group_id, "app_id": app_id, "app_set_id": app_set_id, "app_set_instance_id": app_set_instance_id}
            ))

        else:
            # enroll_state is either "Complete"/"InProgress"
            sce_last_update = sce_gs.last_update

            # Students Enrolled list
            at_rt_dict["has_enrolled"] = []

            if not total_student_enroll_list:
                total_student_enroll_list = []

            student_enroll_list = request.POST.get("student_enroll_list", "")
            if student_enroll_list:
                for each_student_id in student_enroll_list.split(","):
                    each_student_id = ObjectId(each_student_id.strip())
                    if each_student_id not in total_student_enroll_list:
                        total_student_enroll_list.append(each_student_id)
            else:
                student_enroll_list = []

            at_rt_dict["has_enrolled"] = total_student_enroll_list

            if enroll_state == "Complete":
                # For Student-Course Enrollment Approval
                # Create a task for admin(s) of the MIS_admin group
                completed_on = datetime.datetime.now()

                if nussd_course_type == "Foundation Course":
                    task_dict["name"] = "StudentCourseApproval_Task" + "_" + \
                        start_enroll.strftime("%d-%b-%Y") + "_" + end_enroll.strftime("%d-%b-%Y") + \
                        "_FC_" + college_enrollment_code + "_" + start_time.strftime("%b-%Y") + "_" + end_time.strftime("%b-%Y")

                else:
                    task_dict["name"] = "StudentCourseApproval_Task" + "_" + \
                        start_enroll.strftime("%d-%b-%Y") + "_" + end_enroll.strftime("%d-%b-%Y") + \
                        "_" + ann_course_name

                task_dict["name"] = unicode(task_dict["name"])
                task_dict["created_by"] = mis_admin.group_admin[0]
                admin_user = User.objects.get(id=mis_admin.group_admin[0])
                task_dict["created_by_name"] = admin_user.username
                task_dict["modified_by"] = mis_admin.group_admin[0]
                task_dict["contributors"] = [mis_admin.group_admin[0]]

                MIS_GAPP = collection.Node.one({
                    '_type': "GSystemType", 'name': "MIS"
                }, {
                    '_id': 1
                })
                student_course_approval_url_link = ""
                site = Site.objects.get(pk=1)
                site = site.name.__str__()
                student_course_approval_url_link = "http://" + site + "/" + \
                    mis_admin.name.replace(" ", "%20").encode('utf8') + "/dashboard/group"
                task_dict["content_org"] = "\n- Please click [[" + \
                    student_course_approval_url_link + "][here]] to approve students."
                task_dict["content_org"] = unicode(task_dict["content_org"])

                task_dict["start_time"] = completed_on
                task_dict["end_time"] = None

                glist_gst = collection.Node.one({'_type': "GSystemType", 'name': "GList"})
                task_type_node = None
                # Here, GSTUDIO_TASK_TYPES[4] := 'Student-Course Enrollment Approval'
                task_dict["has_type"] = []
                if glist_gst:
                    task_type_node = collection.Node.one({
                        'member_of': glist_gst._id, 'name': GSTUDIO_TASK_TYPES[4]
                    }, {
                        '_id': 1
                    })

                    if task_type_node:
                        task_dict["has_type"].append(task_type_node._id)

                task_dict["Status"] = u"New"
                task_dict["Priority"] = u"High"

                task_dict["group_set"] = [mis_admin._id]

                task_dict["Assignee"] = []
                for each_admin_id in mis_admin.group_admin:
                    task_dict["Assignee"].append(each_admin_id)

                task_node = create_task(task_dict)

                enrollment_task_dict = {}
                approval_task_dict = {}
                for each_task in sce_gs.attribute_set:
                    if "has_approval_task" in each_task:
                        if each_task["has_approval_task"]:
                            approval_task_dict = each_task["has_approval_task"]

                    if "has_enrollment_task" in each_task:
                        if each_task["has_enrollment_task"]:
                            enrollment_task_dict = each_task["has_enrollment_task"]

                if str(task_node._id) not in approval_task_dict:
                    approval_task_dict[str(task_node._id)] = {}

                at_rt_dict["has_approval_task"] = approval_task_dict
                at_rt_dict["has_current_approval_task"] = [task_node._id]

                # Update the enrollment task as "Closed"
                task_dict = {}
                task_dict["_id"] = task_id
                task_dict["Status"] = u"Closed"
                task_dict["modified_by"] = user_id
                task_node = create_task(task_dict)

                # Set completion status for closed enrollment task in StudentCourseEnrollment node's has_enrollment_task
                if str(task_id) in enrollment_task_dict:
                    enrollment_task_dict[str(task_id)] = {
                        "completed_on": completed_on, "completed_by": user_id
                    }
                    at_rt_dict["has_enrollment_task"] = enrollment_task_dict

                # Update StudentCourseEnrollment node's enrollment_status to "APPROVAL" state
                at_rt_dict["enrollment_status"] = u"APPROVAL"

                # Update StudentCourseEnrollment node's last_update field
                sce_gs.last_update = datetime.datetime.today()

            elif enroll_state == "In Progress":
                # Update the enrollment task as "In Progress"
                task_dict["_id"] = task_id
                task_dict["Status"] = u"In Progress"
                task_dict["modified_by"] = user_id
                task_node = create_task(task_dict)

                # Update StudentCourseEnrollment node's last_update field
                sce_gs.last_update = datetime.datetime.today()

        # Save/Update GAttribute(s) and/or GRelation(s)
        for at_rt_name in at_rt_list:
            if at_rt_name in at_rt_dict:
                at_rt_type_node = collection.Node.one({
                    '_type': {'$in': ["AttributeType", "RelationType"]},
                    'name': at_rt_name
                })

                if at_rt_type_node:
                    at_rt_node = None

                    if at_rt_type_node._type == "AttributeType" and at_rt_dict[at_rt_name]:
                        at_rt_node = create_gattribute(sce_gs._id, at_rt_type_node, at_rt_dict[at_rt_name])

                    elif at_rt_type_node._type == "RelationType" and at_rt_dict[at_rt_name]:
                        at_rt_node = create_grelation(sce_gs._id, at_rt_type_node, at_rt_dict[at_rt_name])

        if sce_last_update < sce_gs.last_update:
            collection.update(
                {"_id": sce_gs._id},
                {"$set": {"last_update": sce_gs.last_update}},
                upsert=False, multi=False
            )

        # Very important
        sce_gs.reload()

        return HttpResponseRedirect(reverse(app_name.lower() + ":" + template_prefix + '_app_detail', kwargs={'group_id': group_id, "app_id":app_id, "app_set_id":app_set_id}))

    else:
        # Populate Announced courses of given enrollment
        if not enrollment_closed:
            # Fetch required list of AttributeTypes
            fetch_ats = ["nussd_course_type", "degree_year","degree_name"]

            for each in fetch_ats:
                each = collection.Node.one({
                    '_type': "AttributeType", 'name': each
                }, {
                    '_type': 1, '_id': 1, 'data_type': 1, 'complex_data_type': 1, 'name': 1, 'altnames': 1
                })

                if each["data_type"] == "IS()":
                    # Below code does little formatting, for example:
                    # data_type: "IS()" complex_value: [u"ab", u"cd"] dt:
                    # "IS(u'ab', u'cd')"
                    dt = "IS("
                    for v in each.complex_data_type:
                        dt = dt + "u'" + v + "'" + ", "
                    dt = dt[:(dt.rfind(", "))] + ")"
                    each["data_type"] = dt

                each["data_type"] = eval(each["data_type"])
                each["value"] = None
                req_ats.append(each)

        # Fetch required list of Colleges
        college_cur = None

        title = sce_gs.name

        template = "ndf/student_enroll.html"
        variable = RequestContext(request, {
            'groupid': group_id, 'group_id': group_id,
            'title': title,
            'app_id': app_id, 'app_name': app_name, 'app_collection_set': app_collection_set,
            'app_set_id': app_set_id, 'app_set_instance_id': app_set_instance_id,
            'ATs': req_ats, 'colleges': college_cur,
            'ann_course_list': ann_course_list, "start_enroll": start_enroll, "end_enroll": end_enroll,
            "enrollment_closed": enrollment_closed, "enrollment_reopen": enrollment_reopen
            # 'enrollment_open_ann_course_ids': enrollment_open_ann_course_ids
            # 'nodes':nodes,
        })

        return render_to_response(template, variable)
