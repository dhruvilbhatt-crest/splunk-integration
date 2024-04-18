import ta_databricks_declare  # noqa: F401
import sys
import time
import traceback
import json
import uuid

import databricks_com as com
import databricks_const as const
import databricks_common_utils as utils
from log_manager import setup_logging

from splunklib.searchcommands import (
    dispatch,
    GeneratingCommand,
    Configuration,
    Option,
    validators,
)

APP_NAME = const.APP_NAME
UID = str(uuid.uuid4())
_LOGGER = setup_logging("ta_databricksjob_command", UID)


@Configuration(type="reporting")
class DatabricksJobCommand(GeneratingCommand):
    """Custom Command of databricksjob."""

    # Take input from user using parameters
    job_id = Option(require=True, validate=validators.Integer(0))
    account_name = Option(require=True)
    notebook_params = Option(require=False)

    def generate(self):
        """Generating custom command."""
        _LOGGER.info("Initiating databricksjob command.")
        _LOGGER.info("Job ID: {}".format(self.job_id))
        _LOGGER.info("Notebook Params: {}".format(self.notebook_params))
        info_to_process = {
            "user": self._metadata.searchinfo.username,
            "account_name": self.account_name,
            "created_time": time.time(),
            "param": self._metadata.searchinfo.args,
            "run_id": "-",
            "run_execution_status": "-",
            "output_url": "-",
            "result_url": "-",
            "command_submission_status": "Failed",
            "error": "-",
            "uid": UID
        }

        session_key = self._metadata.searchinfo.session_key

        try:
            databricks_configs = utils.get_databricks_configs(session_key, self.account_name)
            if not databricks_configs:
                ERR_MSG = \
                    "Account '{}' not found. Please provide valid Databricks account.".format(self.account_name)
                raise Exception(ERR_MSG)
            provided_index = databricks_configs.get("index")
            # Get job details
            client = com.DatabricksClient(self.account_name, session_key)

            payload = {
                "job_id": self.job_id,
            }

            _LOGGER.info("Fetching job details before submitting the execution.")
            response = client.databricks_api("get", const.GET_JOB_ENDPOINT, args=payload)

            job_settings = response["settings"]
            tasks_list = list(set(job_settings.keys()))

            if "notebook_task" not in tasks_list:
                raise Exception(
                    "Given job does not contains the notebook task. Hence terminating the execution."
                )
            if (
                "spark_jar_task" in tasks_list
                or "spark_python_task" in tasks_list
                or "spark_submit_task" in tasks_list
            ):
                raise Exception(
                    "Given job contains one of the following task in addition to the notebook task. "
                    "(spark_jar_task, spark_python_task and spark_submit_task) "
                    "Hence terminating the execution."
                )

            # Request for executing the job
            _LOGGER.info("Preparing request body for execution.")
            payload["notebook_params"] = utils.format_to_json_parameters(self.notebook_params)

            _LOGGER.info("Submitting job for execution.")
            response = client.databricks_api("post", const.EXECUTE_JOB_ENDPOINT, data=payload)

            info_to_process.update(response)
            run_id = response["run_id"]
            if run_id:
                _LOGGER.info("run ID returned: {}".format(run_id))
            _LOGGER.info("Successfully executed the job with job ID: {}.".format(self.job_id))

            # Request to get the run_id details
            _LOGGER.info("Fetching details for run ID: {}.".format(run_id))
            args = {"run_id": run_id}
            response = client.databricks_api("get", const.GET_RUN_ENDPOINT, args=args)

            output_url = response.get("run_page_url")
            if output_url:
                result_url = output_url.rstrip("/") + "/resultsOnly"
                info_to_process["output_url"] = output_url
                info_to_process["result_url"] = result_url
                info_to_process["command_submission_status"] = "Success"
                info_to_process["run_execution_status"] = "Initiated"
                _LOGGER.info("Output url returned: {}".format(output_url))

            _LOGGER.info("Successfully executed databricksjob command.")

        except Exception as e:
            _LOGGER.error(e)
            _LOGGER.error(traceback.format_exc())
            info_to_process["error"] = str(e)
            self.write_error(str(e))
            exit(1)

        finally:
            try:
                _LOGGER.info("Ingesting the data into Splunk index: {}".format(provided_index))
                indented_json = json.dumps(info_to_process, indent=4)
                _LOGGER.info("Data to be ingested in Splunk:\n{}".format(indented_json))
                utils.ingest_data_to_splunk(
                    info_to_process, session_key, provided_index, "databricks:databricksjob"
                )
                _LOGGER.info("Successfully ingested the data into Splunk index: {}.".format(provided_index))
            except Exception:
                _LOGGER.error("Error occured while ingesting data into Splunk. Error: {}"
                              .format(traceback.format_exc()))
        yield info_to_process


dispatch(DatabricksJobCommand, sys.argv, sys.stdin, sys.stdout, __name__)
