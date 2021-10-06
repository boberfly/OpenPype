from copy import deepcopy
from pprint import pformat
import arrow
import pandas as pd
from pathlib import Path

from pype.modules.ftrack import ServerAction


class ReportingAnimatorsServer(ServerAction):
    """Report of animators work.

    required modules installation:
        `python -m pip install pandas, openpyxl, xlsxwriter`
    """

    identifier = "report.animator"
    label = "Reporting Animators (Server)"
    description = "Get Animator's report S/24"

    # presets
    role_list = ["Pypeclub", "Administrator", "Project Manager"]
    report_path = None
    task_types = None
    user_group_membership = None
    event_reporting_statuses = None
    events_span_hours = None
    limit_span_to_midnight = None

    # defaults
    __users = None
    __statuses = None
    query_limit = 5

    def discover(self, session, entities, event):
        """Show only on project."""
        return len(entities) == 1 and entities[0].entity_type.lower() in [
            "project",
            "episode",
        ]

    def launch(self, session, entities, event):

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

        # define start time
        start_time = end_time.shift(hours=-self.events_span_hours)

        self.log.debug(
            "__  task_types: {}".format(
                self.task_types))

        # get all taskType id which are 'Animation'
        task_type_ids = self._get_tasks_type_ids(
            self.task_types)

        self.log.info(
            "__  task_type_ids: {}".format(
                task_type_ids))

        self.log.debug(
            "__  event_reporting_statuses: {}".format(
                self.event_reporting_statuses))

        # get all Statuses ids with name `Approved` and `Supervisor Review`
        status_id_filter = self._get_statuses(
            self.event_reporting_statuses
        )

        self.log.debug(
            "__  status_id_filter: {}".format(
                status_id_filter))

        # get all tasks which are having change statuse within time range
        # and are defined task types members
        tasks = self._get_tasks(
            task_type_ids, status_id_filter, start_time, end_time)

        self.log.info("__  tasks: {}".format(pformat(tasks)))
        self.log.info("_" * 100)

        user_report_data = self._generate_report_data(tasks)

        self.log.info("__  user_report_data: {}".format(
            pformat(user_report_data)))
        self.log.info("_" * 100)

        self.create_xlsx_report(user_report_data)

        # get all Users with membership in `Animators` group
        self.log.info("__  self.users: {}".format(
            self.users))

        return True

    def _generate_report_data(self, tasks_data):

        report_data = []
        for _uid, uattrs in self.users.items():
            data = {
                "user": uattrs["name"],
                "task": None,
                "status": None,
                "shot": None,
                "seconds": 0,
                "retakes": 0,
                "retake seconds": 0
            }

            user_data = []
            for _id, attrs in tasks_data.items():
                status = attrs["status_changes"]
                if _uid == status["user_id"]:
                    _data = deepcopy(data)
                    _data.update({
                        "task": attrs["task_name"],
                        "status": status["status_name"],
                        "shot": attrs["shot_name"],
                        "seconds": attrs["seconds"]
                    })
                    if attrs["repair_cycles"]:
                        _data.update({
                            "retakes": 1,
                            "retake seconds": attrs["seconds"]
                        })

                    user_data.append(_data)

            if user_data:
                report_data += user_data
            else:
                report_data.append(data)

        return report_data

    def _get_status_changes(self, events, status_ids, time_from, time_to):

        return_statuses = {
            "reporting": None,
            "all_names": []
        }
        for event in events:
            date = arrow.get(event["date"])
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
                "status_name": status_ids[status_id]["name"]

            }
            self.log.info(f"__ correct status id: `{event['status_id']}`")

        return return_statuses

    def _get_tasks(self, task_type_ids, status_ids, time_from, time_to):

        self.log.info(task_type_ids)

        task_type_ids_str = self.join_query_keys(task_type_ids.keys())

        self.log.info(">> task_type_ids_str: `{}`".format(
            task_type_ids_str))

        # get Tasks
        _query = str(
            'select id, name, type_id, parent_id '
            f'from Task where project_id is "{self._project_id}" '
            f'and type_id in ({task_type_ids_str}) '
            f'and status_changes any ( date <= "{time_to}" '
            f'and date >= "{time_from}") '
            # here could be filter by user ids list
        )
        self.log.info(">> _query: `{}`".format(_query))
        tasks = self._query_from_session(_query)

        self.log.info(">> Query found Tasks - count: `{}`".format(
            len(tasks)))

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

            self.log.info(
                f"__ filter_status_changes: `{filter_status_changes}`")

            shot_data = self._get_shot_data(task["parent_id"])

            tasks_filtred.update({
                task["id"]: {
                    "parent_id": task["parent_id"],
                    "shot_name": shot_data["name"],
                    "seconds": shot_data["seconds"],
                    "task_type": task_type_ids[task["type_id"]]["name"],
                    "task_name": task["name"],
                    "status_changes": filter_status_changes["reporting"],
                    "repair_cycles": len([
                        _st for _st in  filter_status_changes["all_names"]
                        if _st.lower() in ["change requested"]])
                }
            })

        return tasks_filtred

    def _get_shot_data(self, parent_id):
        _query = str(
            'select id, name, object_type.name, custom_attributes '
            'from Shot where id is "{}"').format(parent_id)
        query_output = self._session.query(_query).first()

        _cuattr = query_output["custom_attributes"]
        self.log.info(query_output["name"])
        self.log.info({k: v for k, v in _cuattr.items()})

        frame_start = int(_cuattr["frameStart"])
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

        self.log.info('__> _query: `{}`'.format(_query))

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
        return self._session.query(_query).all()

    def _get_users(self):

        # get all Users
        _query = str(
            'select id, username, last_name, first_name, is_active from User')

        # get group ids of filtered group names
        group_ids = self._get_group_ids(
            self.user_group_membership
        )
        group_ids_str = self.join_query_keys(group_ids.keys())

        if group_ids:
            _query += f' where memberships any (group_id in ({group_ids_str}))'

        users = self._query_from_session(_query)

        self.log.info(">> Collected Users - count: `{}`".format(
            len(users)))

        return {
            user["id"]: {
                "name": "{} {}".format(user["first_name"], user["last_name"]),
                "username": user["username"]
            } for user in users
        }

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

    def create_xlsx_report(self, user_report_data):
        def multiple_dfs(df_list, sheets, file_name, spaces):
            _report_path = Path(self.report_path)
            file_name_path = _report_path.joinpath(file_name)
            writer = pd.ExcelWriter(file_name_path, engine='xlsxwriter')
            row = 0
            for dataframe in df_list:
                dataframe.to_excel(writer, sheet_name=sheets,
                                   startrow=row, startcol=0, index=False)
                row = row + len(dataframe.index) + spaces + 1
            writer.save()

        df = pd.DataFrame(user_report_data)

        # test print
        self.log.info(df.to_string())

        df_sums = df.groupby(
            ['user'], as_index=False).agg({
                'shot': 'count',
                'seconds': 'sum',
                'retakes': 'sum',
                'retake seconds': 'sum'
            })

        df_tasks = df.groupby(
            ['user', 'shot'], as_index=False).agg({
                'status': 'first',
                'seconds': 'sum',
                'retakes': 'sum',
                'retake seconds': 'sum'
            })

        # list of dataframes
        dfs = [df_sums, df_tasks]

        # run function
        multiple_dfs(dfs, "report_day", "animation_seconds_report.xlsx", 2)

def register(session, plugins_presets):
    '''Register plugin. Called when used as an plugin.'''
    ReportingAnimatorsServer(session, plugins_presets).register()
