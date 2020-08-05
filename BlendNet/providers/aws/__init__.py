'''Amazon Web Services
Provide API access to allocate required resources in AWS
Dependencies: boto3
'''

# Exception to notify that the command returned exitcode != 0
class AwsToolException(Exception):
    pass

import os
import sys
import json
import platform
import tempfile
import urllib.request
import subprocess

METADATA_URL = 'http://169.254.169.254/latest/meta-data/'

LOCATION = None # If the script is running in the cloud
AWS_TOOL_PATH = None
AWS_EXEC_PREFIX = ('--output', 'json')
AWS_CONFIGS = None

def _requestMetadata(path):
    req = urllib.request.Request(METADATA_URL+path)
    try:
        while True:
            with urllib.request.urlopen(req, timeout=2) as res:
                if res.getcode() == 503:
                    time.sleep(1)
                    continue
                return res.read().decode('utf-8')
    except:
        return None

def checkLocation():
    '''Returns True if it's the GCP environment'''
    global LOCATION

    if LOCATION is not None:
        return LOCATION

    LOCATION = _requestMetadata('') is not None
    return LOCATION

def checkDependencies():
    return AWS_TOOL_PATH is not None

def _executeAwsTool(*args, fail_ok = False):
    '''Runs the aws tool and returns code and data as tuple'''
    result = subprocess.run(AWS_EXEC_PREFIX + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        if fail_ok:
            return None
        raise AwsToolException('AWS tool returned %d during execution of "%s": %s' % (
            result.returncode, AWS_EXEC_PREFIX + args, result.stderr))

    data = None
    try:
        data = json.loads(result.stdout)
    except json.decoder.JSONDecodeError:
        pass

    return data

def findAWSTool():
    '''Finds absolute path of the aws tool'''
    paths = os.environ['PATH'].split(os.pathsep)
    executable = 'aws'
    extlist = {''}

    if platform.system() == 'Windows':
        extlist = set(os.environ['PATHEXT'].lower().split(os.pathsep))

    for ext in extlist:
        execname = executable + ext
        for p in paths:
            f = os.path.join(p, execname)
            if os.path.isfile(f):
                global AWS_TOOL_PATH, AWS_EXEC_PREFIX
                AWS_TOOL_PATH = f
                AWS_EXEC_PREFIX = (AWS_TOOL_PATH,) + AWS_EXEC_PREFIX
                print('INFO: Found aws tool: %s' % AWS_TOOL_PATH)
                return

def _getConfigs():
    '''Returns dict with aws tool configs'''
    global AWS_CONFIGS
    if not AWS_CONFIGS:
        configs = dict()
        # aws configure returns non-json table, so using direct call
        result = subprocess.run([AWS_TOOL_PATH, 'configure', 'list'], stdout=subprocess.PIPE)
        if result.returncode != 0:
            print('ERROR: Unable to get aws config: %s %s' % (result.returncode, result.stdout))
            return configs

        data = result.stdout.decode('UTF-8').strip()
        for line in data.split(os.linesep)[2:]:
            param = line.split()[0]
            result = subprocess.run([AWS_TOOL_PATH, 'configure', 'get', param], stdout=subprocess.PIPE)
            if result.returncode == 0:
                configs[param] = result.stdout.decode('UTF-8').strip()

        AWS_CONFIGS = configs

    return AWS_CONFIGS


def getProviderInfo():
    configs = dict()
    try:
        configs = _getConfigs()
        useful_quotas = {
            'Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances': 'Std instances',
            'Running On-Demand F instances': 'F instances',
            'Running On-Demand G instances': 'G instances',
            'Running On-Demand Inf instances': 'Inf instances',
            'Running On-Demand P instances': 'P instances',
            'Running On-Demand X instances': 'X instances',
        }

        # Get quotas
        data = _executeAwsTool('service-quotas', 'list-service-quotas',
                               '--service-code', 'ec2', '--query', 'Quotas[].[QuotaName, Value]')

        for q in data:
            if q[0] in useful_quotas:
                configs['Quota: ' + useful_quotas[q[0]]] = '%.1f' % (q[1],)

    except AwsToolException as e:
        configs['ERRORS'] = ['Looks like access to the API is restricted '
                             '- please check your permissions: %s' % e]

    return configs

def getInstanceTypes():
    try:
        data = _executeAwsTool('ec2', 'describe-instance-types',
                               '--query', 'InstanceTypes[].[InstanceType, VCpuInfo.DefaultVCpus, MemoryInfo.SizeInMiB] | sort_by(@, &[0])')
        return dict([ (d[0], '%s vCPUs %s GB RAM' % (d[1], d[2])) for d in data ])
    except AwsToolException as e:
        return {'ERROR': 'Looks like access to the API is restricted '
                         '- please check your permissions: %s' % e}
    return {}

def _createRoles():
    '''Will ensure the required roles are here'''
    role_doc = {
        "Statement": [{
            "Effect": "Allow",
            "Principal": {
                "Service":"ec2.amazonaws.com"
            },
            "Action":"sts:AssumeRole",
        }],
    }

    # Create blendnet-manager role
    try:
        _executeAwsTool('iam', 'create-role',
                        '--role-name', 'blendnet-manager',
                        '--description', 'Automatically created by BlendNet',
                        '--assume-role-policy-document', json.dumps(role_doc))
        print('INFO: Creating the instance profile for role blendnet-manager')
        # Those perms could be neared down - but I think it's too much for now
        _executeAwsTool('iam', 'attach-role-policy',
                        '--role-name', 'blendnet-manager',
                        '--policy-arn', 'arn:aws:iam::aws:policy/AmazonEC2FullAccess')
        _executeAwsTool('iam', 'attach-role-policy',
                        '--role-name', 'blendnet-manager',
                        '--policy-arn', 'arn:aws:iam::aws:policy/AmazonS3FullAccess')
        _executeAwsTool('iam', 'create-instance-profile',
                        '--instance-profile-name', 'blendnet-manager')
        _executeAwsTool('iam', 'add-role-to-instance-profile',
                        '--instance-profile-name', 'blendnet-manager',
                        '--role-name', 'blendnet-manager')
    except AwsToolException as e:
        # The blendnet-manager role is already exists
        pass

    # Create blendnet-agent role
    try:
        _executeAwsTool('iam', 'create-role',
                        '--role-name', 'blendnet-agent',
                        '--description', 'Automatically created by BlendNet',
                        '--assume-role-policy-document', json.dumps(role_doc))
        print('INFO: Creating the instance profile for role blendnet-agent')
        _executeAwsTool('iam', 'attach-role-policy',
                        '--role-name', 'blendnet-agent',
                        '--policy-arn', 'arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess')
        _executeAwsTool('iam', 'create-instance-profile',
                        '--instance-profile-name', 'blendnet-agent')
        _executeAwsTool('iam', 'add-role-to-instance-profile',
                        '--instance-profile-name', 'blendnet-agent',
                        '--role-name', 'blendnet-agent')
    except AwsToolException as e:
        # The blendnet-agent role is already exists
        pass

def _getImageAmi(name = 'debian-10-amd64-daily-*'):
    '''Gets the latest image per name filter'''

    data = _executeAwsTool('ec2', 'describe-images',
                           '--filters', 'Name=name,Values=' + name,
                           '--query', 'sort_by(Images, &CreationDate)[].[Name,ImageId,BlockDeviceMappings[0].DeviceName][-1]')
    print('INFO: Got image %s' % (data[1],))
    return (data[1], data[2])

def _getInstanceId(instance_name):
    '''Gets the instance id based on the tag Name'''
    data = _executeAwsTool('ec2', 'describe-instances',
                           '--filters', 'Name=tag:Name,Values='+instance_name,
                           # Ignore terminated instances
                           '--filters', 'Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped',
                           '--query', 'Reservations[].Instances[].InstanceId')
    if len(data) != 1:
        raise AwsToolException('Error in request of unique instance id with name "%s": %s' % (instance_name, data))

    return data[0]

def createInstanceManager(cfg):
    '''Creating a new instance for BlendNet Manager'''

    _createRoles()

    try:
        _getInstanceId(cfg['instance_name'])
        # If it pass here - means the instance is already existing
        return None
    except AwsToolException:
        # No instance existing - than we can proceed
        pass

    startup_script_file = tempfile.NamedTemporaryFile(mode='w', encoding='UTF-8', newline='\n', suffix='.sh')

    image = _getImageAmi()
    disk_config = [{
        'DeviceName': image[1],
        'Ebs': {
            'DeleteOnTermination': True,
            'VolumeSize': 200,
            'VolumeType': 'standard',
        },
    }]

    options = [
        'ec2', 'run-instances',
        '--tag-specifications', 'ResourceType=instance,Tags=['
            '{Key=Name,Value=%s},'
            '{Key=Session,Value=%s},'
            '{Key=Type,Value=manager}]' % (cfg['instance_name'], cfg['session_id']),
        '--image-id', image[0],
        '--instance-type', cfg['instance_type'],
        '--iam-instance-profile', '{"Name":"blendnet-manager"}',
        '--block-device-mappings', json.dumps(disk_config),
        '--user-data', 'file://' + startup_script_file.name,
    ]

    # TODO: make script overridable
    # TODO: too much hardcode here
    startup_script_file.write('''#!/bin/sh
echo '--> Check for blender dependencies'
dpkg -l libxrender1 libxi6 libgl1
if [ $? -gt 0 ]; then
    apt update
    apt install --no-install-recommends -y libxrender1 libxi6 libgl1
fi

if [ ! -x /srv/blender/blender ]; then
    echo '--> Download & unpack blender'
    echo "{blender_sha256} -" > /tmp/blender.sha256
    curl -fLs "{blender_url}" | tee /tmp/blender.tar.bz2 | sha256sum -c /tmp/blender.sha256 || (echo "ERROR: checksum of the blender binary is incorrect"; exit 1)
    mkdir -p /srv/blender
    tar -C /srv/blender --strip-components=1 -xvf /tmp/blender.tar.bz2
fi

echo '--> Download & run the BlendNet manager'
adduser --shell /bin/false --disabled-password blendnet-user
aws cp --recursive 's3://blendnet-{session_id}/work_manager' "$(getent passwd blendnet-user | cut -d: -f6)"
aws rm --recursive 's3://blendnet-{session_id}/work_manager'
aws cp --recursive 's3://blendnet-{session_id}/blendnet' /srv/blendnet

cat <<'EOF' > /etc/systemd/system/blendnet-manager.service
[Unit]
Description=BlendNet Manager Service
After=network-online.target google-network-daemon.service

[Service]
User=blendnet-user
WorkingDirectory=~
Type=simple
ExecStart=/srv/blender/blender -b -noaudio -P /srv/blendnet/manager.py
Restart=always
TimeoutStopSec=60
StandardOutput=syslog
StandardError=syslog

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl start blendnet-manager.service # We don't need "enable" here
    '''.format(
        blender_url=cfg['dist_url'],
        blender_sha256=cfg['dist_checksum'],
        session_id=cfg['session_id'],
    ))
    startup_script_file.flush()

    # Creating an instance
    print('INFO: Creating manager %s' % (cfg['instance_name'],))
    data = _executeAwsTool(*options)
    # Waiting for the operation to completed
    _executeAwsTool('ec2', 'wait', 'instance-running',
                    '--instance-ids', data[1]['Instances'][0]['InstanceId'])

    return True

def createInstanceAgent(cfg):
    '''Creating a new instance for BlendNet Agent'''

    # TODO: Add spot instance parameter
    #     --instance-market-options file://spot-options.json
    # {
    #   "MarketType": "spot",
    #   "SpotOptions": {
    #     "MaxPrice": "0.02", # Create param for how cheap the instance should be, like 0.33*demand
    #     "SpotInstanceType": "one-time"
    #   }
    # }

    try:
        _getInstanceId(cfg['instance_name'])
        # If it pass here - means the instance is already existing
        return None
    except AwsToolException:
        # No instance existing - than we can proceed
        pass

    startup_script_file = tempfile.NamedTemporaryFile(mode='w', encoding='UTF-8', newline='\n', suffix='.sh')

    image = _getImageAmi()
    disk_config = [{
        'DeviceName': image[1],
        'Ebs': {
            'DeleteOnTermination': True,
            'VolumeSize': 200,
            'VolumeType': 'standard',
        },
    }]

    options = [
        'ec2', 'run-instances',
        '--tag-specifications', 'ResourceType=instance,Tags=['
            '{Key=Name,Value=%s},'
            '{Key=Session,Value=%s},'
            '{Key=Type,Value=agent}]' % (cfg['instance_name'], cfg['session_id']),
        '--image-id', image[0],
        '--instance-type', cfg['instance_type'],
        '--iam-instance-profile', '{"Name":"blendnet-agent"}',
        '--block-device-mappings', json.dumps(disk_config),
        '--user-data', 'file://' + startup_script_file.name,
    ]

    # TODO: make script overridable
    # TODO: too much hardcode here
    startup_script_file.write('''#!/bin/sh
echo '--> Check for blender dependencies'
dpkg -l libxrender1 libxi6 libgl1
if [ $? -gt 0 ]; then
    apt update
    apt install --no-install-recommends -y libxrender1 libxi6 libgl1
fi

if [ ! -x /srv/blender/blender ]; then
    echo '--> Download & unpack blender'
    echo "{blender_sha256} -" > /tmp/blender.sha256
    curl -fLs "{blender_url}" | tee /tmp/blender.tar.bz2 | sha256sum -c /tmp/blender.sha256 || (echo "ERROR: checksum of the blender binary is incorrect"; exit 1)
    mkdir -p /srv/blender
    tar -C /srv/blender --strip-components=1 -xf /tmp/blender.tar.bz2
fi

echo '--> Download & run the BlendNet agent'
adduser --shell /bin/false --disabled-password blendnet-user
aws cp --recursive 's3://blendnet-{session_id}/work_{name}' "$(getent passwd blendnet-user | cut -d: -f6)"
aws cp --recursive 's3://blendnet-{session_id}/blendnet' /srv/blendnet

cat <<'EOF' > /etc/systemd/system/blendnet-agent.service
[Unit]
Description=BlendNet Agent Service
After=network-online.target google-network-daemon.service

[Service]
User=blendnet-user
WorkingDirectory=~
Type=simple
ExecStart=/srv/blender/blender -b -noaudio -P /srv/blendnet/agent.py
Restart=always
TimeoutStopSec=20
StandardOutput=syslog
StandardError=syslog

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl start blendnet-agent.service # We don't need "enable" here
    '''.format(
        blender_url=cfg['dist_url'],
        blender_sha256=cfg['dist_checksum'],
        session_id=cfg['session_id'],
        name=cfg['instance_name'],
    ))
    startup_script_file.flush()

    # Creating an instance
    print('INFO: Creating agent %s' % (cfg['instance_name'],))
    data = _executeAwsTool(*options)
    # Waiting for the operation to completed
    _executeAwsTool('ec2', 'wait', 'instance-running',
                    '--instance-ids', data[1]['Instances'][0]['InstanceId'])

    return True

def startInstance(instance_name):
    '''Start stopped instance with specified name'''

    instance_id = _getInstanceId(instance_name)
    _executeAwsTool('ec2', 'start-instances',
                    '--instance-ids', instance_id)
    # Waiting for the operation to completed
    _executeAwsTool('ec2', 'wait', 'instance-running',
                    '--instance-ids', instance_id)

def stopInstance(instance_name):
    '''Stop instance with specified name'''

    instance_id = _getInstanceId(instance_name)
    _executeAwsTool('ec2', 'stop-instances',
                    '--instance-ids', instance_id)
    # Waiting for the operation to completed
    _executeAwsTool('ec2', 'wait', 'instance-stopped',
                    '--instance-ids', instance_id)

def deleteInstance(instance_name):
    '''Delete the instance with specified name'''

    instance_id = _getInstanceId(instance_name)
    _executeAwsTool('ec2', 'terminate-instances',
                    '--instance-ids', instance_id)
    # Waiting for the operation to completed
    _executeAwsTool('ec2', 'wait', 'instance-terminated',
                    '--instance-ids', instance_id)

def createFirewall(target_tag, port):
    '''Create minimal firewall to access external IP of manager/agent'''
    # TODO
    # By default AWS is wide open

def createBucket(bucket_name):
    '''Creates bucket if it's not exists'''

    _executeAwsTool('s3', 'mb', 's3://' + bucket_name)

    return True

def uploadFileToBucket(path, bucket_name, dest_path = None):
    '''Upload file to the bucket'''

    if not dest_path:
        dest_path = path

    # If the plugin was called from Windows, we need to convert the path separators
    if platform.system() == 'Windows':
        dest_path = pathlib.PurePath(dest_path).as_posix()

    dest_path = 's3://%s/%s' % (bucket_name, dest_path)

    print('INFO: Uploading file to "%s" ...' % (dest_path,))
    _executeAwsTool('s3', 'cp', path, dest_path)

    return True

def uploadDataToBucket(data, bucket_name, dest_path):
    '''Upload file to the bucket'''
    tmp_file = tempfile.NamedTemporaryFile()
    tmp_file.write(data)
    tmp_file.flush()

    uploadFileToBucket(tmp_file.name, bucket_name, dest_path)

    return True


def downloadDataFromBucket(bucket_name, path):
    tmp_file = tempfile.NamedTemporaryFile()

    path = 's3://%s/%s' % (bucket_name, path)

    print('INFO: Downloading file from "%s" ...' % (path,))

    if _executeAwsTool('s3', 'cp', path, tmp_file.name)[0] != 0:
        print('WARN: Downloading failed: %s' % e)
        return None

    return tmp_file.read()

def getResources(session_id):
    '''Get the allocated resources with a specific session_id'''
    out = {'agents':{}}

    def parseInstanceInfo(it):
        name = [ tag['Value'] for tag in it['Tags'] if tag['Key'] == 'Name' ][0]
        return {
            'name': name,
            'ip': it['PublicIpAddress'],
            'internal_ip': it['PrivateIpAddress'],
            'type': it['InstanceType'],
            'started': it['State']['Name'] == 'running',
            'stopped': it['State']['Name'] == 'stopped',
            'created': it['LaunchTime'],
        }

    data = _executeAwsTool('ec2', 'describe-instances',
                           '--filters', 'Name=tag:Session,Values='+session_id,
                           # Ignore terminated instances
                           '--filters', 'Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped',
                           '--query', 'Reservations[].Instances[]')

    for it in data:
        inst = parseInstanceInfo(it)
        it_type = [ tag['Value'] for tag in it['Tags'] if tag['Key'] == 'Type' ][0]
        if it_type == 'manager':
            out['manager'] = inst
        elif it_type == 'agent':
            out['agents'][inst['name']] = inst
        else:
            print('WARN: Unknown type resource instance %s' % inst['name'])

    return out

def getManagerSizeDefault():
    return 't2.micro'

def getAgentSizeDefault():
    return 't2.micro'

def getBucketName(session_id):
    '''Returns the appropriate bucket name'''
    return 'blendnet-%s' % (session_id.lower(),)

def getManagerName(session_id):
    return 'blendnet-%s-manager' % session_id

def getAgentsNamePrefix(session_id):
    return 'blendnet-%s-agent-' % session_id


findAWSTool()

from .Manager import Manager
from .Agent import Agent
from .Instance import Instance
