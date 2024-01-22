#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#


import sys

from airbyte_cdk.entrypoint import launch
from source_mixpanel_export import SourceMixpanelExport

if __name__ == "__main__":
    source = SourceMixpanelExport()
    launch(source, sys.argv[1:])
