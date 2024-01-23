#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

import gzip
import io
import json
import logging
import zipfile
from dataclasses import InitVar, dataclass
from typing import Any, Iterable, Mapping, MutableMapping, Iterator, NamedTuple, List
from collections import defaultdict
from time import sleep

import pendulum
import requests
from airbyte_cdk.sources.declarative.extractors.record_extractor import RecordExtractor
from airbyte_cdk.sources.declarative.schema.json_file_schema_loader import JsonFileSchemaLoader
from airbyte_cdk.sources.declarative.types import Config, Record

logger = logging.getLogger("airbyte")

class TransformationResult(NamedTuple):
    source_name: str
    transformed_name: str

def transform_property_names(property_names: Iterable[str]) -> Iterator[TransformationResult]:
    """
    Transform property names using this rules:
    1. Remove leading "$" from property_name
    2. Resolve naming conflicts, like `userName` and `username`,
    that will break normalization in the future, by adding `_userName`to property name
    """
    lowercase_collision_count = defaultdict(int)
    lowercase_properties = set()

    # Sort property names for consistent result
    for property_name in sorted(property_names):
        property_name_transformed = property_name
        if property_name_transformed.startswith("$"):
            property_name_transformed = property_name_transformed[1:]

        lowercase_property_name = property_name_transformed.lower()
        if lowercase_property_name in lowercase_properties:
            lowercase_collision_count[lowercase_property_name] += 1
            # Add prefix to property name
            prefix = "_" * lowercase_collision_count[lowercase_property_name]
            property_name_transformed = prefix + property_name_transformed

        lowercase_properties.add(lowercase_property_name)
        yield TransformationResult(source_name=property_name, transformed_name=property_name_transformed)

class MixpanelExportExtractor(RecordExtractor):
    """
    Create records from complex response structure
    Issue: https://github.com/airbytehq/airbyte/issues/23145
    """
    """Export API return response in JSONL format but each line is a valid JSON object
    Raw item example:
        {
            "event": "Viewed E-commerce Page",
            "properties": {
                "time": 1623860880,
                "distinct_id": "1d694fd9-31a5-4b99-9eef-ae63112063ed",
                "$browser": "Chrome",                                           -> will be renamed to "browser"
                "$browser_version": "91.0.4472.101",
                "$current_url": "https://unblockdata.com/solutions/e-commerce/",
                "$insert_id": "c5eed127-c747-59c8-a5ed-d766f48e39a4",
                "$mp_api_endpoint": "api.mixpanel.com",
                "mp_lib": "Segment: analytics-wordpress",
                "mp_processing_time_ms": 1623886083321,
                "noninteraction": true
            }
        }
    """
    def iter_dicts(self, lines):
        """
        The incoming stream has to be JSON lines format.
        From time to time for some reason, the one record can be split into multiple lines.
        We try to combine such split parts into one record only if parts go nearby.
        """
        parts = []
        for record_line in lines:
            if record_line == "terminated early":
                logger.warning(f"Couldn't fetch data from Export API. Response: {record_line}")
                return
            try:
                yield json.loads(record_line)
            except ValueError:
                parts.append(record_line)
            else:
                parts = []

            if len(parts) > 1:
                try:
                    yield json.loads("".join(parts))
                except ValueError:
                    pass
                else:
                    parts = []

    def _get_schema_root_properties(self):
        schema_loader = JsonFileSchemaLoader(config=self.config, parameters={"name": self.name})
        schema = schema_loader.get_json_schema()
        return schema["properties"]

    def extract_records(self, response: requests.Response) -> List[Record]:
        # We prefer response.iter_lines() to response.text.split_lines() as the later can missparse text properties embeding linebreaks
        logger.info('Sleep for 120sec due to Mixpanel ratelimit')
        sleep(120)
        for record in self.iter_dicts(response.iter_lines(decode_unicode=True)):
            # transform record into flat dict structure
            item = {"event": record["event"]}
            properties = record["properties"]
            for result in transform_property_names(properties.keys()):
                # Convert all values to string (this is default property type)
                # because API does not provide properties type information
                item[result.transformed_name] = str(properties[result.source_name])

            # convert timestamp to datetime string
            item["time"] = pendulum.from_timestamp(int(item["time"]), tz="UTC").to_iso8601_string()
            #logger.info(f"Item: {item}")
            return [item]

