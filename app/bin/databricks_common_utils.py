import ta_databricks_declare  # noqa: F401
import json
import requests
import traceback
import re
from urllib.parse import urlencode
import databricks_const as const
from log_manager import setup_logging

import splunk.rest as rest
from six.moves.urllib.parse import quote
from solnlib.utils import is_true
from solnlib.credentials import CredentialManager, CredentialNotExistException
import splunklib.results as results
import splunklib.client as client

_LOGGER = setup_logging("ta_databricks_utils")
APP_NAME = const.APP_NAME


def get_databricks_configs(session_key, account_name):
    """
    Get configuration details from ta_databricks_settings.conf.

    :return: dictionary with Databricks fields and values
    """
    _LOGGER.info("Reading configuration file.")
    configs_dict = None
    value = {"name": account_name}
    try:
        _, response_content = rest.simpleRequest(
            "/databricks_get_credentials",
            sessionKey=session_key,
            postargs=value,
            raiseAllErrors=True,
        )
        configs_dict = json.loads(response_content)

        # Setting proxy uri
        if all(
            [
                is_true(configs_dict.get("proxy_enabled")),
                configs_dict.get("proxy_url"),
                configs_dict.get("proxy_type"),
            ]
        ):
            http_uri = configs_dict["proxy_url"]

            if configs_dict.get("proxy_port"):
                http_uri = "{}:{}".format(http_uri, configs_dict.get("proxy_port"))

            if configs_dict.get("proxy_username") and configs_dict.get("proxy_password"):
                http_uri = "{}:{}@{}".format(
                    quote(configs_dict["proxy_username"], safe=""),
                    quote(configs_dict["proxy_password"], safe=""),
                    http_uri,
                )

            http_uri = "{}://{}".format(configs_dict["proxy_type"], http_uri)
            proxy_data = {"http": http_uri, "https": http_uri}
            configs_dict["proxy_uri"] = proxy_data

    except Exception as e:
        _LOGGER.error(
            "Databricks Error : Error occured while fetching databricks account and proxy configs - {}".format(
                e
            )
        )
        _LOGGER.debug(
            "Databricks Error : Error occured while fetching databricks account and proxy configs - {}".format(
                traceback.format_exc()
            )
        )
    return configs_dict


def save_databricks_aad_access_token(account_name, session_key, access_token, client_sec):
    """
    Method to store new AAD access token.

    :return: None
    """
    new_creds = {
        "name": account_name,
        "aad_client_secret": client_sec,
        "aad_access_token": access_token,
        "update_token": True
    }
    try:
        _LOGGER.info("Saving databricks AAD access token.")
        rest.simpleRequest(
            "/databricks_get_credentials",
            sessionKey=session_key,
            postargs=new_creds,
            raiseAllErrors=True,
        )
        _LOGGER.info("Saved AAD access token successfully.")
    except Exception as e:
        _LOGGER.error("Exception while saving AAD access token: {}".format(str(e)))
        _LOGGER.debug(traceback.format_exc())
        raise Exception("Exception while saving AAD access token.")


def get_proxy_clear_password(session_key):
    """
    Get clear password from splunk passwords.conf.

    :return: str/None: proxy password if available else None.
    """
    try:
        manager = CredentialManager(
            session_key,
            app=APP_NAME,
            realm="__REST_CREDENTIAL__#{0}#{1}".format(
                APP_NAME, "configs/conf-ta_databricks_settings"
            ),
        )
        return json.loads(manager.get_password("proxy")).get("proxy_password")
    except CredentialNotExistException:
        return None


def get_proxy_configuration(session_key):
    """
    Get proxy configuration settings.

    :return: proxy configuration dict.
    """
    rest_endpoint = "/servicesNS/nobody/{}/TA_Databricks_settings/proxy".format(APP_NAME)

    _, content = rest.simpleRequest(
        rest_endpoint,
        sessionKey=session_key,
        method="GET",
        getargs={"output_mode": "json"},
        raiseAllErrors=True,
    )

    return json.loads(content)["entry"][0]["content"]


def get_proxy_uri(session_key):
    """
    Generate proxy uri from provided configurations.

    :param session_key: Splunk Session Key
    :param proxy_settings: Proxy configuration dict. Defaults to None.
    :return: if proxy configuration available returns uri string else None.
    """
    _LOGGER.info("Reading proxy configurations from file.")

    proxy_settings = get_proxy_configuration(session_key)

    if proxy_settings.get("proxy_username"):
        proxy_settings["proxy_password"] = get_proxy_clear_password(session_key)

    if all(
        [
            proxy_settings,
            is_true(proxy_settings.get("proxy_enabled")),
            proxy_settings.get("proxy_url"),
            proxy_settings.get("proxy_type"),
        ]
    ):
        http_uri = proxy_settings["proxy_url"]

        if proxy_settings.get("proxy_port"):
            http_uri = "{}:{}".format(http_uri, proxy_settings.get("proxy_port"))

        if proxy_settings.get("proxy_username") and proxy_settings.get(
            "proxy_password"
        ):
            http_uri = "{}:{}@{}".format(
                quote(proxy_settings["proxy_username"], safe=""),
                quote(proxy_settings["proxy_password"], safe=""),
                http_uri,
            )

        http_uri = "{}://{}".format(proxy_settings['proxy_type'], http_uri)

        proxy_data = {"http": http_uri, "https": http_uri}

        _LOGGER.info("Returning proxy configurations.")

        return proxy_data
    else:
        return None


def update_kv_store_collection(splunkd_uri, kv_collection_name, session_key, kv_log_info):
    """
    Create and update KV store collection.

    :param splunkd_uri: Splunk management URI
    :param kv_collection_name: KV Store collection to create/update
    :param session_key: Splunk Session Key
    :param kv_log_info: Information that needs to be updated
    :return: Dictionary with updated value of KV Store update status
    """
    header = {
        "Authorization": "Bearer {}".format(session_key),
        "Content-Type": "application/json",
        "User-Agent": "{}".format(const.USER_AGENT_CONST),
    }

    # Add the log of record into the KV Store
    _LOGGER.info(
        "Adding the command log info to KV Store. Command Log Info: {}".format(kv_log_info)
    )

    kv_update_url = "{}/servicesNS/nobody/{}/storage/collections/data/{}".format(
        splunkd_uri,
        const.APP_NAME,
        kv_collection_name,
    )

    _LOGGER.info(
        "Executing REST call, URL: {}, Payload: {}.".format(kv_update_url, str(kv_log_info))
    )
    response = requests.post(
        kv_update_url,
        headers=header,
        data=json.dumps(kv_log_info),
        verify=const.INTERNAL_VERIFY_SSL,
        timeout=const.TIMEOUT
    )

    if response.status_code in {200, 201}:
        _LOGGER.info("KV Store updated successfully.")
        kv_log_info.update({"kv_status": "KV Store updated successfully"})
    else:
        _LOGGER.info("Error occurred while updating KV Store.")
        kv_log_info.update({"kv_status": "Error occurred while updating KV Store"})

    return kv_log_info


def format_to_json_parameters(params):
    """
    Split the provided string by `||` and make dictionary of that splitted key-value pair string.

    :params: String in the form of "key1=val1||key2=val2"
    :return: dictionary created on the basis of given string
    """
    output_json = {}

    try:
        if params:
            lst = params.split("||")
            for item in lst:
                kv = item.split("=")
                output_json[kv[0].strip()] = kv[1].strip()
    except Exception:
        raise Exception(
            "Invalid format for parameter notebook_params. Provide the value in 'param1=val1||param2=val2' format."
        )

    return output_json


def get_mgmt_port(session_key, logger):
    """Get Management Port."""
    try:
        _, content = rest.simpleRequest(
            "/services/configs/conf-web/settings",
            method="GET",
            sessionKey=session_key,
            getargs={"output_mode": "json"},
            raiseAllErrors=True,
        )
    except Exception as e:
        logger.error(
            "Databricks Get Management Port Error: Error while making request to read"
            " web.conf file. Error: " + str(e)
        )
        logger.debug(
            "Databricks Get Management Port Error: Error while making request to read"
            " web.conf file. Error: " + traceback.format_exc()
        )
    # Parse Result
    try:
        content = json.loads(content)
        content = re.findall(r':(\d+)', content["entry"][0]["content"]["mgmtHostPort"])[0]
        logger.info("Databricks Info: Get managemant port from web.conf is {} ".format(content))
    except Exception as e:
        logger.error("Databricks Error: Error while parsing" " web.conf file. Error: " + str(e))
        logger.debug(
            "Databricks Error: Error while parsing"
            " web.conf file. Error: " + traceback.format_exc()
        )
    return content


def get_current_user(session_key):
    """Get current logged in user."""
    kwargs_oneshot = {"output_mode": "json"}
    searchquery_oneshot = (
        "| rest /services/authentication/current-context splunk_server=local | table username"
    )
    try:
        service = client.connect(port=get_mgmt_port(session_key, _LOGGER), token=session_key)
    except Exception as e:
        _LOGGER.error(
            "Databricks Error: Error while connecting to" " splunklib client. Error: " + str(e)
        )
        _LOGGER.debug(
            "Databricks Error: Error while connecting to"
            " splunklib client. Error: " + traceback.format_exc()
        )

    try:
        oneshotsearch_results = service.jobs.oneshot(searchquery_oneshot, **kwargs_oneshot)

        # Get the results and display them using the JSONResultsReader
        reader = results.JSONResultsReader(oneshotsearch_results)
        for item in reader:
            if isinstance(item, dict) and item.get("username"):
                return item.get("username", None)
        raise Exception("No username found.")
    except Exception as e:
        _LOGGER.error(
            "Databricks Error: Error while fetching" " logged in username. Error: " + str(e)
        )
        _LOGGER.debug(
            "Databricks Error: Error while fetching"
            " logged in username. Error: " + traceback.format_exc()
        )


def get_aad_access_token(
    session_key,
    account_name,
    aad_tenant_id,
    aad_client_id,
    aad_client_secret,
    proxy_settings=None,
    retry=1,
):
    """
    Method to acquire a new AAD access token.

    :param session_key: Splunk session key
    :return: access token
    """
    token_url = const.AAD_TOKEN_ENDPOINT.format(aad_tenant_id)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "{}".format(const.USER_AGENT_CONST),
    }
    _LOGGER.debug("Request made to the Databricks from Splunk user: {}".format(get_current_user(session_key)))
    data_dict = {"grant_type": "client_credentials", "scope": const.SCOPE}

    data_dict["client_id"] = aad_client_id
    data_dict["client_secret"] = aad_client_secret
    data_encoded = urlencode(data_dict)
    while retry:
        try:
            resp = requests.post(
                token_url,
                headers=headers,
                data=data_encoded,
                proxies=proxy_settings,
                verify=const.VERIFY_SSL,
                timeout=const.TIMEOUT
            )
            resp.raise_for_status()
            response = resp.json()
            aad_access_token = response.get("access_token")
            if not all([aad_tenant_id, aad_client_id, aad_client_secret]):
                save_databricks_aad_access_token(
                    account_name, session_key, aad_access_token, aad_client_secret
                )
            return aad_access_token
        except Exception as e:
            retry -= 1
            if "resp" in locals():
                error_code = resp.json().get("error_codes")
                if error_code:
                    error_code = str(error_code[0])
                if error_code in list(const.ERROR_CODE.keys()):
                    msg = const.ERROR_CODE[error_code]
                elif str(resp.status_code) in list(const.ERROR_CODE.keys()):
                    msg = const.ERROR_CODE[str(resp.status_code)]
                elif resp.status_code not in (200, 201):
                    msg = (
                        "Response status: {}. Unable to validate Azure Active Directory Credentials."
                        "Check logs for more details.".format(str(resp.status_code))
                    )
            else:
                msg = (
                    "Unable to request Databricks instance. "
                    "Please validate the provided Databricks and "
                    "Proxy configurations or check the network connectivity."
                )
                _LOGGER.error("Error while trying to generate AAD access token: {}".format(str(e)))
                _LOGGER.debug(traceback.format_exc())
            _LOGGER.error(msg)
            if retry == 0:
                return msg, False


def get_user_agent():
    """Method to get user agent."""
    return "{}".format(const.USER_AGENT_CONST)
