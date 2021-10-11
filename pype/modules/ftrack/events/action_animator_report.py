import os
import tempfile
from copy import deepcopy
from pprint import pformat
import arrow
import pandas as pd
from pathlib import Path
import json
from pype.modules.ftrack import ServerAction


class ReportingAnimatorsServer(ServerAction):
    """Report of animators work.

    required modules installation:
        `python -m pip install pandas, openpyxl, xlsxwriter`

    Add following to config `presets\ftrack\plugins\server.json`
    ```
    {
        "ReportingAnimatorsServer": {
            "role_list": [
                "pypeclub",
                "administrator",
                "project manager"
            ],
            "task_types": [
                "Animation"
            ],
            "user_group_membership": [
                "Animation Team 1",
                "Animation Team 2"
            ],
            "event_reporting_statuses": [
                "Approved",
                "Lead Review"
            ],
            "event_retake_statuses": [
                "Retake"
            ],
            "events_span_hours": 24,
            "limit_span_to_midnight": true
        }
    }
    ```
    """

    identifier = "report.generator.animator"
    label = "Reporting Animators"
    description = "Get Animator's report S/24"

    # presets
    role_list = ["Pypeclub", "Administrator", "Project Manager"]
    task_types = None
    user_group_membership = None
    event_reporting_statuses = None
    events_span_hours = None
    limit_span_to_midnight = None
    event_retake_statuses = None

    # defaults
    __users = None
    __statuses = None
    query_limit = None

    def discover(self, session, entities, event):
        """Show only on project."""
        return len(entities) == 1 and entities[0].entity_type.lower() in [
            "project",
            "episode",
        ]

    def launch(self, session, entities, event):

        user_entity = session.query(
            "User where id is {}".format(event["source"]["user"]["id"])
        ).one()
        job_entity = session.create("Job", {
            "user": user_entity,
            "status": "running",
            "data": json.dumps({
                "description": "Animation Report Generator running..."
            })
        })
        session.commit()

        try:
            result = self.action_process(job_entity, session, entities, event)
        except Exception as _E:
            msg = "Animation Report Generator failed: {}".format(_E)

            self.log.error(msg, exc_info=True)

            result = {
                "success": False,
                "message": msg
            }
            job_entity["data"] = json.dumps({
                "description": msg
            })
            job_entity["status"] = "failed"
            session.commit()

        return result

    def add_file_to_job(self, job_entity, session, description, file_path):

        self.log.info("file_path: `{}`".format(file_path))

        job_entity["data"] = json.dumps({
            "description": description
        })

        job_entity["status"] = "done"


        component_name = "{}_{}".format(
            "AnimationReport_XLSX_file",
            self.date_stamp
        )

        session._configure_locations()
        self.log.info("component_name: `{}`".format(component_name))

        # Query `ftrack.server` location where component will be stored
        location = session.query(
            "Location where name is \"ftrack.server\""
        ).one()
        self.log.info("location: `{}`".format(location))

        # session.commit()


        component = session.create_component(
            file_path,
            data={"name": component_name},
            location=location
        )
        self.log.info("Component ID: `{}`".format(component["id"]))
        session.create(
            "JobComponent",
            {
                "component_id": component["id"],
                "job_id": job_entity["id"]
            }
        )
        session.commit()

        # Delete temp file
        os.remove(file_path)


    def action_process(self, job_entity, session, entities, event):

        self._session = session
        self.selected_entity = entities[0]
        self._project_id = self.selected_entity["project_id"]

        # define time range for events to be fitred within
        end_time = arrow.now()
        # this will limit it to midnight if the day action is processed
        # so if processing is started at 5pm the limit will put it back to
        # 0 am of the day
        if self.limit_span_to_midnight:
            end_time = end_time.floor('day')

        self.date_stamp = end_time.format('YYYY-MM-DD_HH-mm')

        # define start time
        start_time = end_time.shift(hours=-self.events_span_hours)

        # get all taskType id for defined types
        task_type_ids = self._get_tasks_type_ids(
            self.task_types)

        # get all Statuses ids with name defined names
        status_id_filter = self._get_statuses(
            self.event_reporting_statuses
        )

        # get all users which are in group Animators with assigned tasks
        # get all tasks which are having change statuse within time range
        # and are defined task types members
        tasks = self._get_tasks(
            task_type_ids, status_id_filter, start_time, end_time)

        report_data = self._generate_report_data(tasks)

        file_name_path = self.create_xlsx_report(report_data)

        self.add_file_to_job(
            job_entity, session,
            "Download Animation Report file here",
            file_name_path
        )

        return {
                "success": True,
                "message": "Animation Reporting Finished"
            }

    def _generate_report_data(self, tasks_data):
        group_statuses = []
        report_data = []
        for _uid, uattrs in self.users.items():
            user_name = uattrs["name"]
            group = uattrs["group_name"]
            empty_data = {
                "user": "",
                "shot": None,
                "task": None,
                "status": None,
                "seconds": None,
                "retakes": None,
                "retake seconds": None,
                "group": None
            }

            # user with no activity basic data
            data = deepcopy(empty_data)
            data["user"] = user_name
            data["group"] = group

            user_data = []
            for _id, attrs in tasks_data.items():
                if _uid == attrs["user_id"]:
                    _data = deepcopy(empty_data)
                    _data.update({
                        "user": "--",
                        "shot": attrs["shot_name"],
                        "task": attrs["task_name"],
                        "status": attrs["status_name"]
                    })
                    if not attrs["wip"]:
                        # add following only if it is not work in progress
                        _data.update({
                            "seconds": attrs["seconds"]})
                        if attrs["repair_cycles"]:
                            _data.update({
                                "retakes": 1,
                                "retake seconds": attrs["seconds"]
                            })

                    user_data.append(_data)

            if user_data:
                header_data = self.make_user_header_info(user_data)
                # update header data with user name and remove status
                header_data.update({
                    "user": user_name,
                    "status": "",
                    "group": group
                })
                # add header sumary row
                report_data.append(header_data)
                group_statuses.append(header_data)
                # add full list of task data
                report_data += user_data
            else:
                report_data.append(data)

            # add space
            report_data.append(empty_data)

        return report_data, group_statuses

    def make_user_header_info(self, user_report_data):
        df = pd.DataFrame(user_report_data)

        # test print
        self.log.info(df.to_string())

        df_sums = df.groupby(
            ['user'], as_index=False).agg({
                'shot': 'count',
                "task": 'count',
                'status': 'last',
                'seconds': 'sum',
                'retakes': 'sum',
                'retake seconds': 'sum'
            })

        return df_sums.to_dict('records').pop()

    def _get_status_changes(self, events, status_ids, time_from, time_to):

        return_statuses = {
            "reporting": None,
            "all_names": []
        }

        # first of all sort events by dates
        _events = {event["date"]: event for event in events}
        _sorted_events_dates = sorted(_events.keys())
        # then iterate all events
        for _date in _sorted_events_dates:
            date = arrow.get(_date)
            event = _events[_date]
            status_id = event["status_id"]

            if (date > time_to and date < time_from):
                return_statuses["all_names"].append(
                    self.statuses[status_id]["name"])
                continue

            if status_id not in status_ids.keys():
                return_statuses["all_names"].append(
                    self.statuses[status_id]["name"])
                continue

            return_statuses["reporting"] = {
                "user_id": event["user_id"],
                "status_id": status_id,
                "status_name": status_ids[status_id]["name"],
                "parent_id": event["parent_id"]

            }

        return return_statuses

    def _get_tasks(self, task_type_ids, status_ids, time_from, time_to):
        users = self.users
        task_type_ids_str = self.join_query_keys(task_type_ids.keys())
        user_ids_str = self.join_query_keys(users.keys())
        # get Tasks
        _query = str(
            'select id, name, type_id, parent_id, assignments '
            f'from Task where project_id is "{self._project_id}" '
            f'and type_id in ({task_type_ids_str}) '
            f'and assignments any ( resource_id in ({user_ids_str})) '
            f'and status_changes any ( date <= "{time_to}" '
            f'and date >= "{time_from}") '
        )
        tasks = self._query_from_session(_query)
        self.log.info(f"Task amount: `{len(tasks)}`")

        # filter out all tasks which are not in selected entity as parent
        tasks_filtred = {}
        for task in tasks:
            in_correct_parent = any(
                link.get("id") == self.selected_entity["id"]
                for link in task["link"]
            )

            if not in_correct_parent:
                continue

            filter_status_changes = self._get_status_changes(
                task["status_changes"], status_ids, time_from, time_to
            )

            assigned_user_id = self._assigned_user(task)

            self.log.info(f"Task: `{task}`")

            shot_data = self._get_shot_data(task["parent_id"])

            self.log.info(f"filter_status_changes: `{filter_status_changes}`")

            if not shot_data:
                continue

            # if reporting status is available add it or add last used status
            # also define if the task is in progress or in review
            if filter_status_changes.get("reporting"):
                status_name = filter_status_changes["reporting"]["status_name"]
                wip = False
            else:
                wip = True
                status_name = filter_status_changes["all_names"][-1]

            users = self.users
            user_name = users[assigned_user_id]["name"]
            self.log.info(
                f"Adding Task: {shot_data['name']}:{task['name']}>{user_name}")

            tasks_filtred.update({
                task["id"]: {
                    "wip": wip,
                    "user_name": user_name,
                    "user_id": assigned_user_id,
                    "parent_id": task["parent_id"],
                    "shot_name": shot_data["name"],
                    "seconds": shot_data["seconds"],
                    "task_type": task_type_ids[task["type_id"]]["name"],
                    "task_name": task["name"],
                    "status_name": status_name,
                    "repair_cycles": len([
                        _st for _st in  filter_status_changes["all_names"]
                        if _st.lower() in [
                            _s.lower() for _s in self.event_retake_statuses]])
                }
            })

        return tasks_filtred

    def _get_shot_data(self, parent_id):
        _query = str(
            'select id, name, object_type.name, custom_attributes '
            'from Shot where id is "{}"').format(parent_id)
        query_output = self._session.query(_query).first()

        if not query_output:
            return None

        self.log.info(f"Parent Shot entity: `{query_output['name']}`")

        _cuattr = query_output["custom_attributes"]

        frame_start = int(_cuattr.get("frameStart") or 1001)
        frame_end = int(_cuattr["frameEnd"])
        fps = float(_cuattr.get("fps") or 25)

        return {
            "name": query_output["name"],
            "seconds": abs(((frame_start - frame_end + 1) / fps))
        }

    def _get_tasks_type_ids(self, name=None):
        base_query = 'select id, name from Type'
        return self._create_query_with_name_filter(name, base_query, "name")

    def _get_group_ids(self, name=None):
        base_query = 'select id, name from Group'
        return self._create_query_with_name_filter(name, base_query, "name")

    def _get_statuses(self, name=None):
        name = name or []
        statuses = self.statuses

        return_data = {}
        for id, data in statuses.items():
            if str(data["name"]).lower() in [nm.lower() for nm in name]:
                return_data.update({
                    id: data
                })
        return return_data

    def _create_query_with_name_filter(
            self, name, base_query, ouptup_attrs=None):

        names_str = ''
        if not name:
            names_str = None
        if isinstance(name, (list, tuple)):
            names_str = self.join_query_keys(name)
        else:
            names_str = '"{}"'.format(name)

        _query = base_query

        if names_str:
            _query += ' where name in ({})'.format(names_str)

        returned_entities = self._query_from_session(_query)

        if ouptup_attrs and isinstance(ouptup_attrs, (list, tuple)):
            return_dict = {}
            for ent in returned_entities:
                return_dict.update({
                    ent["id"]: {attr: ent.get(attr)
                                for attr in ouptup_attrs}
                })
            return return_dict

        elif ouptup_attrs and isinstance(ouptup_attrs, str):
            return_dict = {}
            for ent in returned_entities:
                return_dict.update({
                    ent["id"]: {ouptup_attrs: ent.get(ouptup_attrs)}
                })
            return return_dict

        else:
            return returned_entities

    def _query_from_session(self, _query, use_limit=True):
        if self.query_limit and use_limit:
            _query += ' limit {}'.format(self.query_limit)

        self.log.info(f"Query used: `{_query}`")

        return self._session.query(_query).all()

    def _get_users(self):

        _users = {}

        # get all Users
        _query_base = str(
            'select id, username, last_name, first_name, is_active from User')

        groups = self._get_group_ids(self.user_group_membership)
        for group_id in groups:
            _query = (_query_base
                      + f' where memberships any (group_id is {group_id})')

            users = self._query_from_session(_query, use_limit=False)

            _users.update({
                user["id"]: {
                    "group_name": groups[group_id]["name"],
                    "name": "{} {}".format(user["first_name"], user["last_name"]),
                    "username": user["username"]
                } for user in users if user["is_active"]
            })
        return _users

    def _assigned_user(self, task):
        return task["assignments"][-1]["resource_id"]

    @property
    def users(self):
        if not self.__users:
            self.__users = self._get_users()
        return self.__users

    def _get_all_statuses(self):

        # get all Users
        _query = str(
            'select id, name from Status')

        statuses = self._query_from_session(_query, use_limit=False)

        return {
            status["id"]: {
                "name": status["name"],
            } for status in statuses
        }

    @property
    def statuses(self):
        if not self.__statuses:
            self.__statuses = self._get_all_statuses()
        return self.__statuses

    def create_xlsx_report(self, report_data):
        def multiple_dfs(df_list, sheet_name, spaces, horisontal=False):
            # Create temp file where traceback will be stored
            temp_obj = tempfile.NamedTemporaryFile(
                mode="w", prefix=("pype_task_report_" + self.date_stamp + "_"),
                suffix=".xlsx", delete=False
            )
            temp_obj.close()
            file_name_path = temp_obj.name
            writer = pd.ExcelWriter(file_name_path, engine='xlsxwriter')
            row = 0
            column = 0
            for dataframe in df_list:
                dataframe.to_excel(writer, sheet_name=sheet_name,
                                   startrow=row, startcol=column, index=False)
                if horisontal:
                    column = column + len(dataframe.columns) + spaces + 1
                else:
                    row = row + len(dataframe.index) + spaces + 1
            writer.save()

            return file_name_path

        user_report_data, group_data = report_data
        df_users = pd.DataFrame(user_report_data)
        df_groups = pd.DataFrame(group_data)

        # test print
        self.log.info(df_users.to_string())
        self.log.info(df_groups.to_string())

        # get group data statistics
        df_groups_grouped = df_groups.groupby(
            ['group'], as_index=False).agg({
                'user': 'count',
                'shot': 'sum',
                'seconds': 'sum',
                'retakes': 'sum',
                'retake seconds': 'sum'
            })

        # list of dataframes
        dfs = [df_users, df_groups_grouped]

        # run function
        return multiple_dfs(
            dfs, "report_day", 2, True)

def register(session, plugins_presets):
    '''Register plugin. Called when used as an plugin.'''
    ReportingAnimatorsServer(session, plugins_presets).register()
