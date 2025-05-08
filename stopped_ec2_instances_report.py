import boto3
import pandas as pd
import logging
import traceback
import os
from datetime import datetime
import re

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
LOG_LEVEL = logging.INFO

# --- Logging Setup ---
logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s: %(asctime)s: %(message)s")
LOGGER = logging.getLogger()

# --- U.S. Regions Only ---
US_REGIONS = [
    "us-east-1", "us-east-2",
    "us-west-1", "us-west-2",
    "us-gov-west-1", "us-gov-east-1",
    "us-iso-east-1", "us-isob-east-1"
]

def sanitize_sheet_name(name):
    return re.sub(r'[\\/*?:\[\]]', "_", name)[:31]

def get_instance_root_volume_size(ec2_client, instance_id):
    try:
        vols = ec2_client.describe_volumes(Filters=[{
            'Name': 'attachment.instance-id',
            'Values': [instance_id]
        }])['Volumes']
        for vol in vols:
            if any(att['Device'] == '/dev/xvda' or att['Device'] == '/dev/sda1' for att in vol['Attachments']):
                return vol['Size']
    except Exception:
        return None
    return None

def extract_instance_data(instance, region, root_vol_size):
    tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
    return {
        'Instance ID': instance['InstanceId'],
        'Name': tags.get('Name', ''),
        'State': instance['State']['Name'],
        'Instance Type': instance['InstanceType'],
        'Platform': instance.get('Platform', 'Linux/UNIX'),
        'Region': region,
        'Root Volume Size (GiB)': root_vol_size,
        'Launch Time': instance['LaunchTime']
    }

def get_account_alias(session):
    try:
        iam = session.client("iam")
        aliases = iam.list_account_aliases()["AccountAliases"]
        return aliases[0] if aliases else session.client("sts").get_caller_identity()["Account"]
    except Exception:
        return session.client("sts").get_caller_identity()["Account"]

if __name__ == "__main__":
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"{timestamp}_stopped_terminated_ec2.xlsx"
    writer = pd.ExcelWriter(output_file, engine="xlsxwriter")

    for profile in TARGET_PROFILES:
        LOGGER.info(f"Processing profile: {profile}")
        try:
            session = boto3.Session(profile_name=profile, region_name=DEFAULT_REGION)
            account_name = sanitize_sheet_name(get_account_alias(session))
            all_instances = []

            for region in US_REGIONS:
                try:
                    ec2 = session.client("ec2", region_name=region)
                    paginator = ec2.get_paginator('describe_instances')
                    pages = paginator.paginate(
                        Filters=[{
                            'Name': 'instance-state-name',
                            'Values': ['stopped', 'terminated']
                        }]
                    )
                    for page in pages:
                        for reservation in page['Reservations']:
                            for instance in reservation['Instances']:
                                root_vol_size = get_instance_root_volume_size(ec2, instance['InstanceId'])
                                instance_data = extract_instance_data(instance, region, root_vol_size)
                                all_instances.append(instance_data)
                except Exception as e:
                    LOGGER.warning(f"Failed in region {region}: {e}")

            if all_instances:
                df = pd.DataFrame(all_instances)
                df.sort_values(by=['Region', 'State', 'Launch Time'], inplace=True)
                sheet_name = account_name
                df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
                LOGGER.info(f"Wrote {len(df)} instances to sheet: {sheet_name}")
            else:
                pd.DataFrame(columns=[
                    'Instance ID', 'Name', 'State', 'Instance Type',
                    'Platform', 'Region', 'Root Volume Size (GiB)', 'Launch Time'
                ]).to_excel(writer, sheet_name=account_name[:31], index=False)
                LOGGER.info(f"No stopped/terminated instances for profile: {profile}")

        except Exception as e:
            LOGGER.error(f"Error processing profile {profile}: {e}")
            LOGGER.error(traceback.format_exc())

    writer.close()
    LOGGER.info(f"Report saved to: {output_file}")
