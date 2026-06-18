#!/usr/bin/env python3
"""
DataHub Governance Script — Tag Ownerless Datasets
Finds all datasets with no owners and adds a "needs-owner" tag to them.
Supports DRY_RUN mode (default: true) for safe preview before applying.
Idempotent: safe to run multiple times.
"""

import os
import sys
import json
import logging
import requests
from typing import Optional

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ─── Configuration (from environment variables) ──────────────────────────────
# DATAHUB_GMS_URL = os.environ.get("DATAHUB_GMS_URL", "http://datahub-datahub-gms:8080") # name of svc of backend inside k8s
DATAHUB_GMS_URL = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080") # name of svc of backend inside k8s
DATAHUB_TOKEN   = os.environ.get("DATAHUB_TOKEN", "eyJhbGciOiJIUzI1NiJ9.eyJhY3RvclR5cGUiOiJVU0VSIiwiYWN0b3JJZCI6ImRhdGFodWIiLCJ0eXBlIjoiUEVSU09OQUwiLCJ2ZXJzaW9uIjoiMiIsImp0aSI6Ijk2MTA4MGNkLTI5OTktNDFlNy05YjhhLTgwOTMzMjg2NjY0OCIsInN1YiI6ImRhdGFodWIiLCJleHAiOjE3ODQzNjUyMjEsImlzcyI6ImRhdGFodWItbWV0YWRhdGEtc2VydmljZSJ9.oY13bSGm7X11B08mSe23-WDmI5UDWmT7h2Fpy-J7YbQ")
DRY_RUN         = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
BATCH_SIZE      = int(os.environ.get("BATCH_SIZE", "100"))
TAG_NAME        = os.environ.get("TAG_NAME", "needs-owner")
TAG_DESCRIPTION = os.environ.get("TAG_DESCRIPTION", "Dataset has no assigned owner and requires one.")

GQL_URL = f"{DATAHUB_GMS_URL}/api/graphql" # its graphql endpoint in backend and grapiql in webui 

# ─── HTTP Headers ────────────────────────────────────────────────────────────
def get_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if DATAHUB_TOKEN:
        headers["Authorization"] = f"Bearer {DATAHUB_TOKEN}"
    return headers


# ─── GraphQL Helper ──────────────────────────────────────────────────────────
# the main function that sends GraphQL queries as http requests to backend /api/graphiql
def run_gql(query: str, variables: dict = None) -> dict: 
    """Execute a GraphQL query/mutation against DataHub GMS."""
    payload = {"query": query} # put the query itself in the payload
    if variables:
        payload["variables"] = variables # if there variables, add them to the payload variables key 

    try:
        resp = requests.post( # make post request to /api/graphiql with the query
            GQL_URL,
            headers=get_headers(),
            json=payload, # the query
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        log.error("Cannot connect to DataHub GMS at %s — is it running? Error: %s", GQL_URL, e)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        log.error("HTTP error from DataHub GMS: %s — Response: %s", e, resp.text)
        sys.exit(1)

    data = resp.json() # the result of the query store in data , if the respone have errors cause wrong query syntax so exit with error
    if "errors" in data:
        log.error("GraphQL errors: %s", json.dumps(data["errors"], indent=2))
        sys.exit(1)

    return data.get("data", {})


# ─── Step 1: Ensure the tag exists in DataHub ────────────────────────────────
# for idempotency check if the tag already exists , if not create it , if DRY_RUN simulate the creation

TAG_URN = f"urn:li:tag:{TAG_NAME}"# tag name here represents the id of the tag


# query of create tag
CREATE_TAG_MUTATION = """
mutation CreateTag($input: CreateTagInput!) {
  createTag(input: $input)
}
"""
# query of get specific tag with urn
GET_TAG_QUERY = """
query GetTag($urn: String!) {
  tag(urn: $urn) {
    urn
    properties {
      name
      description
    }
  }
}
"""

# fun of ensure_tag_exists 
# def ensure_tag_exists() -> None:

#     """
#     Idempotent: check if tag exists, create only if it does not.
#     This prevents failures on repeated runs.
#     """
#     log.info("Checking if tag '%s' exists (URN: %s) ...", TAG_NAME, TAG_URN)
#     result = run_gql(GET_TAG_QUERY, {"urn": TAG_URN})
#     tag = result.get("tag")

#     if tag and tag.get("urn"):
#         log.info("Tag '%s' already exists — skipping creation.", TAG_NAME)
#         return

#     if DRY_RUN:
#         log.info("[DRY-RUN] Would create tag '%s' with description: %s", TAG_NAME, TAG_DESCRIPTION)
#         return

#     log.info("Creating tag '%s' ...", TAG_NAME)
#     run_gql(CREATE_TAG_MUTATION, {
#         "input": {
#             "id": TAG_NAME, # not be random id 
#             "name": TAG_NAME,
#             "description": TAG_DESCRIPTION,
#         }
#     })
#     log.info("Tag '%s' created successfully.", TAG_NAME)

def ensure_tag_exists() -> None:
    """
    Idempotent: ensures the tag exists before using it.
    Uses TAG search instead of tag(urn) because some DataHub versions
    return a false-positive object for missing tags.
    """

    log.info("Checking if tag '%s' exists (URN: %s) ...", TAG_NAME, TAG_URN)

    SEARCH_TAG_QUERY = """
    query SearchTag($query: String!) {
      search(input: {
        type: TAG,
        query: $query,
        start: 0,
        count: 10
      }) {
        searchResults {
          entity {
            ... on Tag {
              urn
              name
            }
          }
        }
      }
    }
    """

    result = run_gql(SEARCH_TAG_QUERY, {"query": TAG_NAME})

    results = (
        result.get("search", {})
              .get("searchResults", [])
    )

    exists = any(
        r.get("entity", {}).get("urn") == TAG_URN
        for r in results
    )

    if exists:
        log.info("Tag '%s' already exists — skipping creation.", TAG_NAME)
        return

    if DRY_RUN:
        log.info("[DRY-RUN] Would create tag '%s'", TAG_NAME)
        return

    log.info("Creating tag '%s' ...", TAG_NAME)

    CREATE_TAG_MUTATION = """
    mutation CreateTag($input: CreateTagInput!) {
      createTag(input: $input)
    }
    """

    run_gql(CREATE_TAG_MUTATION, {
        "input": {
            "id": TAG_NAME,
            "name": TAG_NAME,
            "description": TAG_DESCRIPTION,
        }
    })

    log.info("Tag '%s' created successfully.", TAG_NAME)




# ─── Step 2: Paginate all datasets ───────────────────────────────────────────
# query all dataset to search on them if they have no owner add needs-owner tag to them

# # this query won't work 
# SEARCH_DATASETS_QUERY = """
# query SearchDatasets($start: Int!, $count: Int!) {
#   search(input: {
#     type: DATASET,
#     query: "*",
#     start: $start,
#     count: $count
#   }) {
#     total
#     searchResults {
#       entity {
#         urn
#         type
#         ... on Dataset {
#           name
#           ownership {
#             owners {
#               owner {
#                 urn
#               }
#             }
#           }
#           tags {
#             tags {
#               tag {
#                 urn
#               }
#             }
#           }
#         }
#       }
#     }
#   }
# }
# """

SEARCH_DATASETS_QUERY = """
query SearchDatasets($start: Int!, $count: Int!) {
  search(input: {
    type: DATASET,
    query: "*",
    start: $start,
    count: $count
  }) {
    total
    searchResults {
      entity {
        urn
        type
        ... on Dataset {
          name
          ownership {
            owners {
              owner {
                __typename
              }
            }
          }
          tags {
            tags {
              tag {
                urn
              }
            }
          }
        }
      }
    }
  }
}
"""


def get_all_datasets() -> list[dict]:
    """
    Fetch ALL datasets from DataHub using cursor-based pagination.
    Returns a list of entity dicts from the GraphQL response.
    """
    all_entities = []
    start = 0

    log.info("Fetching datasets from DataHub (batch size: %d) ...", BATCH_SIZE)

    while True:
        data = run_gql(SEARCH_DATASETS_QUERY, {"start": start, "count": BATCH_SIZE})
        search = data.get("search", {})
        total = search.get("total", 0)  
        results = search.get("searchResults", [])

        if start == 0:
            log.info("Total datasets found: %d", total)

        for r in results:
            entity = r.get("entity") # entity is each dataset ,  so get just entities 
            if entity:
                all_entities.append(entity)

        fetched_so_far = start + len(results)
        log.info("Fetched %d / %d datasets ...", fetched_so_far, total)

        if fetched_so_far >= total or not results: # we finish when finish all datasets or when current batch - results is empty
            break

        start += BATCH_SIZE # take another batch

    log.info("Finished fetching. Total datasets loaded: %d", len(all_entities))
    return all_entities


# ─── Step 3: Filter ownerless datasets ───────────────────────────────────────
# take entity check if it has no owner or have 
def is_ownerless(entity: dict) -> bool:
    """Return True if the dataset has no owners assigned."""
    ownership = entity.get("ownership") # owner ship can be null or can be one or can be list of owners
    if not ownership: # if null 
        return True
    owners = ownership.get("owners", []) # if empty list return true else false
    return len(owners) == 0 


def already_tagged(entity: dict) -> bool:
    """Return True if the dataset already has the needs-owner tag (idempotency check)."""
    tags_wrapper = entity.get("tags")
    if not tags_wrapper:
        return False
    tag_associations = tags_wrapper.get("tags", [])
    for assoc in tag_associations:
        tag = assoc.get("tag", {})
        if tag.get("urn") == TAG_URN: # if have our tag -> needs-owner so return true else false
            return True
    return False


# ─── Step 4: Apply tag to a dataset ──────────────────────────────────────────
ADD_TAG_MUTATION = """
mutation AddTag($input: TagAssociationInput!) {
  addTag(input: $input)
}
"""


def apply_tag(urn: str) -> None:
    """Apply the needs-owner tag to a single dataset URN."""
    run_gql(ADD_TAG_MUTATION, {
        "input": {
            "tagUrn": TAG_URN,
            "resourceUrn": urn,
        }
    })


# ─── Main Logic ──────────────────────────────────────────────────────────────
def main() -> None:
    log.info("=" * 60)
    log.info("DataHub Governance: Tag Ownerless Datasets")
    log.info("GMS URL   : %s", DATAHUB_GMS_URL)
    log.info("Tag       : %s", TAG_NAME)
    log.info("Batch size: %d", BATCH_SIZE)
    log.info("DRY RUN   : %s", DRY_RUN)
    log.info("=" * 60)

    if DRY_RUN:
        log.info(">>> DRY-RUN MODE — No changes will be made <<<")

    # Step 1: Make sure the tag exists on DataHub
    ensure_tag_exists()

    # Step 2: Get all datasets
    all_datasets = get_all_datasets()

    if not all_datasets:
        log.warning("No datasets found in DataHub. Is sample data loaded?")
        sys.exit(0)

    # Step 3: Filter — no owners AND not already tagged
    ownerless = []
    already_done = []
    has_owner = []

    for entity in all_datasets: #loop on all datasets entity by entity see if it has no owner
        if not is_ownerless(entity): # has owner
            has_owner.append(entity["urn"])
        elif already_tagged(entity): # has our tag needs-owner
            already_done.append(entity["urn"])
        else:
            ownerless.append(entity) # no owner & not already tagged

    log.info("-" * 60)
    log.info("Results:")
    log.info("  Datasets with owners          : %d", len(has_owner))
    log.info("  Already tagged (needs-owner)  : %d (skipping — idempotent)", len(already_done))
    log.info("  Ownerless, not yet tagged     : %d", len(ownerless))
    log.info("-" * 60)

    if not ownerless: # if onwerless is empty so all of them have owners so exit
        log.info("Nothing to do — all ownerless datasets are already tagged.")
        sys.exit(0)

    # Step 4: Apply or preview
    tagged_count = 0
    failed_urns = []

    for entity in ownerless:
        urn = entity["urn"]  # urn of entity - dataset
        name = entity.get("name", urn)

        if DRY_RUN: # simulate you tagged them 
            log.info("[DRY-RUN] Would tag: %s (%s)", name, urn)
            tagged_count += 1
        else:
            try:
                apply_tag(urn)
                log.info("Tagged: %s (%s)", name, urn)
                tagged_count += 1
            except SystemExit:
                log.error("Failed to tag: %s (%s)", name, urn)
                failed_urns.append(urn)

    log.info("=" * 60)
    if DRY_RUN:
        log.info("DRY-RUN complete. Would have tagged %d dataset(s).", tagged_count)
        log.info("Re-run with DRY_RUN=false to apply changes.")
    else:
        log.info("Done. Tagged %d dataset(s).", tagged_count)
        if failed_urns:
            log.error("Failed to tag %d dataset(s):", len(failed_urns))
            for u in failed_urns:
                log.error("  - %s", u)
            sys.exit(1)

    log.info("=" * 60)


if __name__ == "__main__":
    main()