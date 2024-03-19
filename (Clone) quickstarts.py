# Databricks notebook source
# MAGIC %md 
# MAGIC # Purpose
# MAGIC
# MAGIC The notebook provides a convenient way to benchmark and measure query response time across different configurations of SQL Severless Warehouse using [Databricks SQL Connector](https://docs.databricks.com/en/dev-tools/python-sql-connector.html). You can quickly evaluate query performance with varying warehouse sizes or different warehouse types such as Serverless, Pro, or Classic.
# MAGIC
# MAGIC > You should have existing data available in the workspace to proceed. If you don't have available data, the default data used in the notebook is tpch data in samples catalog along with tpch sample queries in queries folder of this repo.
# MAGIC
# MAGIC ## Getting Started
# MAGIC
# MAGIC 1. Run the "Set up" cells below.
# MAGIC 2. Update the parameters based on your purpose or keep the default values to see how it works.
# MAGIC 3. After making the necessary changes, click "Run" or "Run All" to execute the entire notebook with the updated parameters.
# MAGIC
# MAGIC ## Parameters
# MAGIC
# MAGIC * benchmark_choice: This parameter allows you to choose between running the benchmark on a single warehouse ("one-warehouse") or multiple warehouses ("multiple-warehouses"). The default warehouse specification (for `one-warehouse` option) can be chosen from below. 
# MAGIC
# MAGIC   * If you choose `multiple-warehouses` option, you will run benchmark on serverless, classic, pro warehouse with the same size
# MAGIC   
# MAGIC   * If you choose `multiple-warehouses-size` option, you will run benchmark on multiple warehouses with different sizes. You will have the option to specify the warehouse sizes in Cell 7 on this notebook
# MAGIC
# MAGIC Specify the warehouse info:
# MAGIC
# MAGIC * constants.warehouse_prefix: This parameter specifies the name prefix of the warehouse. When running the benchmark, the warehouse size and type will be attached to the warehouse_name before spinning up warehouse
# MAGIC
# MAGIC * constants.warehouse_type: This parameter allows you to select the type of warehouse for the benchmark. The available options are "serverless", "pro", and "classic".
# MAGIC
# MAGIC * constants.warehouse_size: This parameter determines the size of the warehouse. You can choose from different predefined sizes such as "2X-Small", "X-Small", "Small", "Medium", "Large", "X-Large", "2X-Large", "3X-Large", and "4X-Large".
# MAGIC
# MAGIC Specify the location of your existing data below:
# MAGIC
# MAGIC * constants.catalog_name: This parameter specifies the name of the catalog where the benchmark schema is located.
# MAGIC
# MAGIC * constants.schema_name: This parameter defines the name of the schema within the catalog where the benchmark tables are stored.
# MAGIC
# MAGIC Upload your `queries` to queries folder, and provide the query path below:
# MAGIC
# MAGIC * constants.query_path: This parameter specifies the path to the query file or directory containing the benchmark queries.
# MAGIC
# MAGIC Specify the constants.concurrency level, cluster size, and whether to enable result cache:
# MAGIC
# MAGIC * constants.query_repetition_count: This parameter determines the number of times each query in the benchmark will be executed.
# MAGIC
# MAGIC * constants.concurrency: This parameter sets the level of constants.concurrency, indicating how many queries can be executed simultaneously.
# MAGIC
# MAGIC * constants.max_clusters: This parameter specifies the maximum number of clusters that the warehouse can be scaled up to. We recommend 1 cluster for 10 concurrent queries (maximum 25 clusters)
# MAGIC
# MAGIC * constants.results_cache_enabled (default False): if False the query won't be served from result cache

# COMMAND ----------

# MAGIC %md
# MAGIC # Set up

# COMMAND ----------

spark.conf.set("spark.sql.execution.arrow.pyspark.enabled", "false")
spark.conf.get("spark.sql.execution.arrow.pyspark.enabled")

# COMMAND ----------

# MAGIC %pip install -r requirements.txt -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import pandas as pd
import logging
from beaker import benchmark
from databricks.sdk import WorkspaceClient
import os
import requests

logger = logging.getLogger()

HOSTNAME = spark.conf.get('spark.databricks.workspaceUrl')
TOKEN = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()

# COMMAND ----------

from constants import *

# COMMAND ----------

dbutils.widgets.removeAll()

# COMMAND ----------

create_widgets_benchmark(dbutils)

# COMMAND ----------

# MAGIC %md
# MAGIC # Benchmark

# COMMAND ----------

constants = Constants(
  **get_widget_values_benchmark(dbutils)
)

constants

# COMMAND ----------

if constants.benchmark_choice == "multiple-warehouses-choice":
  warehouse_sizes = constants.warehouse_size.split(",")
else:
  # Take only the first warehouse option if multiple-warehouses-choice is not selected
  warehouse_sizes = constants.warehouse_size.split(",")
  constants.warehouse_size = warehouse_sizes[0]

print(constants.warehouse_size)
print(warehouse_sizes)

# COMMAND ----------

logger.setLevel(logging.WARNING)

tables = spark.catalog.listTables(f"{constants.catalog_name}.{constants.schema_name}")
tables = [table.name for table in tables]
tables

# COMMAND ----------

from beaker import spark_fixture, sqlwarehouseutils
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED

logger.setLevel(logging.INFO)

def get_warehouse(hostname, token, warehouse_name):
  sql_warehouse_url = f"https://{hostname}/api/2.0/sql/warehouses"
  response = requests.get(sql_warehouse_url, headers={"Authorization": f"Bearer {token}"})
  
  if response.status_code == 200:
    for warehouse in response.json()['warehouses']:
      if warehouse['name'] == warehouse_name:
        return(warehouse['id'])
  else:
    print(f"Error: {response.json()['error_code']}, {response.json()['message']}")

def run_benchmark(warehouse_type=constants.warehouse_type, warehouse_size=constants.warehouse_size):

    warehouse_name = f"{constants.warehouse_prefix} {warehouse_type} {warehouse_size}"
    # Get warehouse id
    warehouse_id = get_warehouse(HOSTNAME, TOKEN, warehouse_name)

    if warehouse_id:
        # Use your own warehouse
        print(f"--Use current warehouse `{warehouse_name}` {warehouse_id}--")
        http_path = f"/sql/1.0/warehouses/{warehouse_id}"
        new_warehouse_config = None
    else:
        # Specify a new warehouse
        http_path = None
        print(f"--Specify new warehouse `{warehouse_name}`--")
        new_warehouse_config = {
            "name": warehouse_name,
            "type": "warehouse",
            "warehouse": warehouse_type,
            "runtime": "latest",
            "size": warehouse_size,
            "min_num_clusters": 1,
            "max_num_clusters": constants.max_clusters,
            "enable_photon": True,
        }

    bm = benchmark.Benchmark()
    bm.setName(f"Benchmark {warehouse_name}")
    bm.setHostname(HOSTNAME)
    bm.setWarehouseToken(TOKEN)

    if http_path:
        bm.setWarehouse(http_path)
    else:
        bm.setWarehouseConfig(new_warehouse_config)


    bm.setCatalog(constants.catalog_name)
    bm.setSchema(constants.schema_name)

    # bm.preWarmTables(tables)

    bm.setConcurrency(constants.concurrency)
    bm.setQueryRepeatCount(constants.query_repetition_count)
    bm.results_cache_enabled = constants.results_cache_enabled

    if os.path.isdir(constants.query_path):
        bm.setQueryFileDir(constants.query_path)
    else:
        bm.setQueryFile(constants.query_path)
    
    beaker_metrics, history_metrics = bm.execute()
    bm.sql_warehouse.close_connection()
    bm.stop_warehouse(bm.warehouse_id)
    return  beaker_metrics, history_metrics


def run_multiple_benchmarks():
    """
    Run multiple benchmarks for different warehouse types.
    
    Returns:
    - combined_metrics_pdf (pandas.DataFrame): A Pandas DataFrame containing the combined metrics results from all the benchmarks.
    """

    with ThreadPoolExecutor(max_workers=3) as executor:
        warehouse_types = ["serverless", "pro", "classic"]
        futures = [executor.submit(run_benchmark, warehouse_type, constants.warehouse_size) for warehouse_type in warehouse_types]
        wait(futures, return_when=ALL_COMPLETED)
    
    combined_metrics_pdf = pd.DataFrame()
    for future in futures:
        if combined_metrics_pdf.empty:
            combined_metrics_pdf = future.result()
        else:
            combined_metrics_pdf = pd.concat([combined_metrics_pdf, future.result()])

    return combined_metrics_pdf

def run_multiple_benchmarks_size(warehouse_sizes):
    """
    Run multiple benchmarks for different warehouse sizes.
    
    Parameters:
    - warehouse_sizes (list): A list of warehouse sizes to be benchmarked.
    
    Returns:
    - combined_metrics_pdf (pandas.DataFrame): A Pandas DataFrame containing the combined metrics results from all the benchmarks.
    """

    with ThreadPoolExecutor(max_workers=len(warehouse_sizes)) as executor:
        futures = [executor.submit(run_benchmark, constants.warehouse_type, warehouse_size) for warehouse_size in warehouse_sizes]
        wait(futures, return_when=ALL_COMPLETED)
    
    combined_metrics_pdf = pd.DataFrame()
    for future in futures:
        if combined_metrics_pdf.empty:
            combined_metrics_pdf = future.result()
        else:
            combined_metrics_pdf = pd.concat([combined_metrics_pdf, future.result()])

    return combined_metrics_pdf

# COMMAND ----------

if constants.benchmark_choice == "one-warehouse":
  beaker_metrics, history_metrics = run_benchmark(constants.warehouse_type)

# elif constants.benchmark_choice == "multiple-warehouses":
#   metrics_pdf = run_multiple_benchmarks()

# elif constants.benchmark_choice == "multiple-warehouses-size":
#   metrics_pdf = run_multiple_benchmarks_size(warehouse_sizes)

# COMMAND ----------

from pandas import json_normalize

def clean_metrics(beaker_metrics, history_metrics):
    logging.info(f"Clean Query Metrics")
    beaker_pdf = pd.DataFrame(beaker_metrics)

    history_pdf = pd.DataFrame(history_metrics)
    history_pdf_clean = json_normalize(history_pdf['metrics'].apply(str).apply(eval))
    history_pdf_clean["query_text"] = history_pdf["query_text"]
    
    # metrics_pdf =  beaker_pdf[['id', 'warehouse_name','query']].drop_duplicates().merge(history_pdf_clean, left_on='query', right_on='query_text', how='inner')
    # metrics_pdf = clean_query_metrics(raw_metrics_pdf)
    return beaker_pdf, history_pdf_clean

# COMMAND ----------

beaker_pdf = pd.DataFrame(beaker_metrics)

history_pdf = pd.DataFrame(history_metrics)
history_pdf_clean = json_normalize(history_pdf['metrics'].apply(str).apply(eval))
history_pdf_clean["query_text"] = history_pdf["query_text"]
history_pdf_clean["query_id"] = history_pdf["query_id"]

# COMMAND ----------

display(beaker_pdf)

# COMMAND ----------

display(history_pdf)

# COMMAND ----------

metrics_pdf =  beaker_pdf[['id', 'warehouse_name','query']].drop_duplicates().merge(history_pdf_clean, left_on='query', right_on='query_text', how='inner').drop('query_text', axis=1)
display(metrics_pdf)

# COMMAND ----------

# MAGIC %md
# MAGIC Below graph shows average duration of all queries in the warehouse history from start to end of benchmark, broken down by warehouses

# COMMAND ----------

import plotly.graph_objects as go

# Group the metrics by 'id' and 'warehouse_name' and calculate the average total_time_ms
grouped_metrics = metrics_pdf.groupby(['id', 'warehouse_name']).mean(numeric_only=True)['total_time_ms'].reset_index()

# Create a stacked bar chart using Plotly
fig = go.Figure()

# Iterate over each unique warehouse_name and add a bar for each warehouse
for warehouse_name in grouped_metrics['warehouse_name'].unique():
    warehouse_data = grouped_metrics[grouped_metrics['warehouse_name'] == warehouse_name]
    fig.add_trace(go.Bar(
        x=warehouse_data['id'],
        y=warehouse_data['total_time_ms'],
        name=warehouse_name
    ))

# Set the layout of the chart
fig.update_layout(
    xaxis_title='ID',
    yaxis_title='Total Time (ms)',
    title='Query Metrics by Warehouse'
)

# Display the chart
fig.show()

# COMMAND ----------


