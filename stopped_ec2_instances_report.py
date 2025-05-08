import boto3
import pandas as pd
import logging
import traceback
from datetime import datetime
import os

# --- Configuration ---
TARGET_PROFILES = [
    "ir-doi-gpsi", "doi-trimble-prod-com", "imagery-data-management", "nhd-plus",
    "doi-keycloak-prod-com-1", "doi-keycloak-prod-com-2", "ir-doi-osmre", "boem-odgip-prod-com",
    "fws-iris-mgmt-com", "fws-iris-prod-com", "fws-ecosphere-sandbox-com", "fws-ecosphere-common",
    "fws-ecosphere-dev", "fws-ecosphere-prod-com", "fws-ecosphere-test", "usgs-wim-wma", "gomcollab",
    "usgs-wim-prod", "usgs-wim-fws-cbrs", "usgs-wim-main", "usgs-wim-streamstats",
    "usgs-wim-fws-wetlands", "osmre-eamlis-prod-com", "usgs-shira-prod-com", "fws-tracs-prod",
    "bor-mp-gis", "doi-datainventory-dev-com", "fema-disasters-prod", "fema-disasters-sandbox",
    "fema-rmd-prod", "fema-rmd-1", "fema-rmd-2", "bia-bogs-prod-com", "nps-doi-1", "nps-doi-2",
    "nps-mapbox-prod-com", "nps-mapbox-dev-com", "nps-cartos-prod-com", "nps-cartos-dev-com",
    "nps-usmp-prod-com", "oas-safecom-staging", "doi-usdaiipp-prod-com", "usgs-trails-dev",
    "usgs-trails-prod", "doi-borgis-prod-com", "doi-ocio-ac-prod-com"
]
DEFAULT_REGION = "us-east-1"
OUTPUT_FILENAME = f"ec2_stopped_terminated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def get_us_regions(session):
    ec2 = session.client("ec2", region_name=DEFAULT_REGION)
    return [r["RegionName"] for r in ec2.describe_regions(AllRegions=False)["Regions"] if r["RegionName"].startswith("us-")]

def collect_instance_details(session, region):
    instances_data = []
    ec2 = session.client("ec2", region_name=region)

    try:
        paginator = ec2.get_paginator("describe_instances")
        page_iterator = paginator.paginate(
            Filters=[{
                'Name': 'instance-state-name',
                'Values': ['stopped', 'terminated']
            }]
        )

        for page in page_iterator:
            for reservation in page["Reservations"]:
                for instance in reservation["Instances"]:
                    instance_id = instance.get("InstanceId")
                    state = instance.get("State", {}).get("Name")
                    instance_type = instance.get("InstanceType")
                    name = next((tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"), "")
                    platform = instance.get("Platform", "Linux/UNIX")
                    root_device = instance.get("RootDeviceType", "")
                    volumes = []
                    for mapping in instance.get("BlockDeviceMappings", []):
                        ebs = mapping.get("Ebs", {})
                        volumes.append(f"{mapping['DeviceName']}:{ebs.get('VolumeId')}:{ebs.get('VolumeSize','?')}GB:{ebs.get('DeleteOnTermination')}")
                    eip_info = ""
                    try:
                        network_interfaces = instance.get("NetworkInterfaces", [])
                        for ni in network_interfaces:
                            if "Association" in ni and "PublicIp" in ni["Association"]:
                                eip_info = ni["Association"]["PublicIp"]
                    except Exception:
                        pass

                    instances_data.append({
                        "Region": region,
                        "InstanceId": instance_id,
                        "Name": name,
                        "State": state,
                        "Type": instance_type,
                        "Platform": platform,
                        "RootDeviceType": root_device,
                        "EBSVolumes": ", ".join(volumes),
                        "ElasticIP": eip_info
                    })
    except Exception as e:
        logger.error(f"Error in region {region}: {e}")
        logger.debug(traceback.format_exc())
    return instances_data

# --- Main ---
if __name__ == "__main__":
    all_instances = []

    for profile in TARGET_PROFILES:
        logger.info(f"Scanning profile: {profile}")
        try:
            session = boto3.Session(profile_name=profile)
            account_id = session.client("sts").get_caller_identity().get("Account")
            regions = get_us_regions(session)

            for region in regions:
                logger.info(f"  - Scanning region: {region}")
                instance_info = collect_instance_details(session, region)
                for i in instance_info:
                    i["AccountId"] = account_id
                    i["Profile"] = profile
                all_instances.extend(instance_info)

        except Exception as e:
            logger.error(f"Failed to process profile {profile}: {e}")
            logger.debug(traceback.format_exc())

    if all_instances:
        df = pd.DataFrame(all_instances)
        df.sort_values(by=["AccountId", "Region", "State"], inplace=True)
        df.to_excel(OUTPUT_FILENAME, index=False)
        logger.info(f"Report written to: {OUTPUT_FILENAME}")
    else:
        logger.info("No stopped or terminated instances found.")
