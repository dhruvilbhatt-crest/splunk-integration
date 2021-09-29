# Databricks notebook source
# MAGIC %md
# MAGIC This is a companion notebook to <a href ="https://databricks.com/blog/2020/10/05/detecting-criminals-and-nation-states-through-dns-analytics.html"> Detecting Criminals and Nation States through DNS Analytics </a> blog.  
# MAGIC We invite your feedback at cybersecurity@databricks.com .

# COMMAND ----------

# MAGIC %md 
# MAGIC ###How to run this notebook. 
# MAGIC - Go to the Clusters pull down, towards the top left corner
# MAGIC - Click on My Cluster
# MAGIC - From the notebook menu, click: Run All
# MAGIC - If your not running the notebok in community edition, set the flag below to false.

# COMMAND ----------

# MAGIC %pip install tldextract geoip2 dnstwist

# COMMAND ----------

#Set this flag to false if you are not running this notebook in community edition
community_edition = True

# COMMAND ----------

if community_edition == True:
    default_file_path = '/databricks/driver/data'
else:
    default_file_path = '/dbfs/FileStore/tables/datasets'

# COMMAND ----------

# MAGIC %scala
# MAGIC displayHTML("""<iframe src="https://drive.google.com/file/d/1ZMu8nFMuCzPZonOJmib8TpFR9JNypS0L/preview" frameborder="0" height="480" width="640"></iframe>
# MAGIC """)

# COMMAND ----------

# MAGIC %md
# MAGIC # 1. Pre-Ingestion: pDNS AutoLoader setup
# MAGIC In our lab setup, the <a href ="https://www.farsightsecurity.com/technical/passive-dns/passive-dns-faq/#:~:text=%22Passive%20DNS%22%20or%20%22Passive,can%20be%20indexed%20and%20queried.">passive DNS (pDNS) </a> data is posted to AWS S3 buckets at regular intervals. We monitor the S3 bucket and import the data using AutoLoader. We use multiple tables to stage, schematize and store analytics results. Here is the TLDR on table naming.
# MAGIC - Bronze: Raw data
# MAGIC - Silver: Schematized and enriched data
# MAGIC - Gold:  Detections and alerts
# MAGIC Why do this? Short version: so you can always go back to the source, refine your analytics over time, and never lose any data. <a href="https://databricks.com/blog/2019/08/14/productionizing-machine-learning-with-delta-lake.html"> And the long version.</a>
# MAGIC 
# MAGIC We will use Databricks AutoLoader. There is a lot of code below. But mostly boiler plate for AutoLoader. You can read all about AutoLoader <a href="https://docs.databricks.com/spark/latest/structured-streaming/auto-loader.html#language-scala">here </a>. If you are unfamiliar with Scala and you don't want to read all about AutoLoader, we are with you. Here's why Autoloader is important:
# MAGIC - File state management: Incrementally processes new files as they land in S3. You don’t need to manage any state information on what files arrived.
# MAGIC - Scalable: Track  new files arriving by leveraging cloud services and is scalable even with millions of files in a directory.
# MAGIC - Easy to use: Automatically set up notification and message queue services required for incrementally processing the files. No setup needed from you.
# MAGIC 
# MAGIC We will setup to load the pDNS data from AWS S3 bucket into a <b>Bronze table</b>. We won't actually load any data until the next section, titled, <b>"Loading the data"</b>

# COMMAND ----------

#install our libraries, you will need to install these manually if you are running mlruntime
#dbutils.library.installPyPI("tldextract")
#dbutils.library.installPyPI("geoip2")
#dbutils.library.installPyPI("dnstwist")
#dbutils.library.installPyPI("mlflow")

# COMMAND ----------

# MAGIC %scala
# MAGIC //In this section we are telling AutoLoader what kind of schema to expect
# MAGIC import org.apache.spark.sql.functions._
# MAGIC import org.apache.spark.sql.types._
# MAGIC 
# MAGIC //The pDNS schema and detail is here: https://tools.ietf.org/id/draft-dulaunoy-dnsop-passive-dns-cof-04.html
# MAGIC //rrnme, rrtype, time_first, time_last, count, bailwick and rdata are fields in the data.
# MAGIC val schema = new StructType()
# MAGIC     .add("rrname", StringType, true)
# MAGIC     .add("rrtype", StringType, true)
# MAGIC     .add("time_first", LongType, true)
# MAGIC     .add("time_last", LongType, true)
# MAGIC     .add("count", LongType, true)
# MAGIC     .add("bailiwick", StringType, true)
# MAGIC     .add("rdata", ArrayType(StringType, true), true)
# MAGIC  

# COMMAND ----------

#In this segment, we'll download all of the datasets we need in order to be able to run our notebook
#These datasets include anonymized DNS data, a GeoIP lookup database, a threat feed and domains generated by dnstwist for our enrichment pipeline
#We also include the top 100k domains on alexa, a list of dictionary words, a list of dga domains to train a DGA model

# COMMAND ----------

# MAGIC %sh mkdir data
# MAGIC mkdir data/latest
# MAGIC mkdir model
# MAGIC curl -o data/dns_events.json https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/data/dns_events.json 
# MAGIC curl -o data/GeoLite2_City.mmdb https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/data/GeoLite2_City.mmdb
# MAGIC curl -o data/ThreatDataFeed.txt https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/data/ThreatDataFeed.txt
# MAGIC curl -o data/alexa_100k.txt https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/data/alexa_100k.txt
# MAGIC curl -o data/dga_domains_header.txt https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/data/dga_domains_header.txt
# MAGIC curl -o data/domains_dnstwists.csv https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/data/domains_dnstwists.csv
# MAGIC curl -o data/words.txt https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/data/words.txt
# MAGIC curl -o data/latest/dns_test_1.json https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/data/dns_test_1.json
# MAGIC curl -o model/MLmodel https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/model/MLmodel
# MAGIC curl -o model/conda.yaml https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/model/conda.yaml
# MAGIC curl -o model/python_model.pkl https://raw.githubusercontent.com/zaferbil/dns-notebook-datasets/master/model/python_model.pkl

# COMMAND ----------

#Copy the downloaded data into the FileStore for this workspace
dbutils.fs.cp("file:///databricks/driver/data","dbfs:/FileStore/tables/datasets/",True)
dbutils.fs.cp("file:///databricks/driver/model","dbfs:/FileStore/tables/model/",True)

# COMMAND ----------

# MAGIC %scala
# MAGIC //In this segment, we are specifying where the data is and what type of data it is.
# MAGIC //You can see the json format, the path and the AWS region
# MAGIC val df = (spark.read.format("json").schema(schema)
# MAGIC             .load(f"dbfs:/FileStore/tables/datasets/dns_events.json")
# MAGIC          )

# COMMAND ----------

# MAGIC %scala
# MAGIC //The rdata field has an array element. This isn't very useful if you want to parse it or string search it.
# MAGIC //So we create a new field called rdatastr. You can see the difference in the two fields in the sample output below.
# MAGIC val df_enhanced = df.withColumn("rdatastr",concat_ws(",",col("rdata")))
# MAGIC display(df_enhanced)

# COMMAND ----------

# MAGIC %scala
# MAGIC //Here we specify the format of the data to be written, and the destination path
# MAGIC //This is still just setup - Data has not been posted to the Bronze table yet. 
# MAGIC df_enhanced.write.format("delta").mode("overwrite").option("mergeSchema", "true").save(f"dbfs:/FileStore/tables/tables/bronze/delta/DNS_raw")

# COMMAND ----------

# MAGIC %md
# MAGIC # 1.a Pre-Ingestion: URLHaus threat feed setup
# MAGIC We will be using URLHaus threat feeds with our pDNS data. This section shows you how to ingest the URLHaus feed.
# MAGIC You already saw how to do ingest setup with Scala. This example will use python.
# MAGIC 
# MAGIC For this setup, we need to do two things:
# MAGIC - Define functions for field extractions so we can extract the registered_domain_extract, domain_extract and suffix_extract fields from the URLHaus feeds. We will do this by creating  <a href="https://docs.databricks.com/spark/latest/spark-sql/udf-python.html"> user defined functions (UDF)</a>.
# MAGIC - Create an enriched schema and save it to a silver table.

# COMMAND ----------

# MAGIC %python
# MAGIC #We will extract the registered_domain_extract and domain_extract fields from the URLHaus feeds.
# MAGIC import tldextract
# MAGIC import numpy as np
# MAGIC 
# MAGIC def registred_domain_extract(uri):
# MAGIC     ext = tldextract.extract(uri)
# MAGIC     if (not ext.suffix):
# MAGIC         return " "
# MAGIC     else:
# MAGIC         return ext.registered_domain
# MAGIC       
# MAGIC def domain_extract(uri):
# MAGIC     ext = tldextract.extract(uri)
# MAGIC     if (not ext.suffix):
# MAGIC         return " "
# MAGIC     else:
# MAGIC         return ext.domain
# MAGIC 
# MAGIC #The next three lines are registering our user defined functions(UDF) in the Databricks runtime environment 
# MAGIC spark.udf.register("registred_domain_extract", registred_domain_extract)
# MAGIC spark.udf.register("domain_extract", domain_extract)

# COMMAND ----------

# MAGIC %python
# MAGIC #We specify the source location of the URLHaus feed, the csv format, and declare that the csv has field labels in a header
# MAGIC threat_feeds_location = "dbfs:/FileStore/tables/datasets/ThreatDataFeed.txt"
# MAGIC threat_feeds_raw = spark.read.format("csv").option("header", "true").load(threat_feeds_location)
# MAGIC #Display a sample so we can check to see it makes sense
# MAGIC display(threat_feeds_raw)

# COMMAND ----------

# MAGIC %python
# MAGIC # We create a new enrched view by extracting the domain name from the URL using the domain_extractor user defined function from the previous section.
# MAGIC threat_feeds_raw.createOrReplaceTempView("threat_feeds_raw")
# MAGIC threat_feeds_enriched = spark.sql("select *, domain_extract(url) as domain  from threat_feeds_raw").filter("char_length(domain) >= 2")
# MAGIC #The sample display shows the new field "domain"
# MAGIC display(threat_feeds_enriched)

# COMMAND ----------

# MAGIC %python
# MAGIC #We save our new, enriched schema 
# MAGIC threat_feeds_enriched.write.format("delta").mode('overwrite').option("mergeSchema", "true").save("dbfs:/FileStore/tables/tables/silver/delta/enriched_threat_feeds")

# COMMAND ----------

# MAGIC %md
# MAGIC # 1.b DNS Twist Setup for detecting lookalike domains
# MAGIC We will use <a href="https://github.com/elceef/dnstwist">dnstwist</a> to monitor lookalike domains that adversaries can use to attack you. 
# MAGIC Using <a href="https://github.com/elceef/dnstwist">dnstwist</a> you can detect <a href="https://capec.mitre.org/data/definitions/630.html">typosquatters</a>, phishing attacks, fraud, and brand impersonation. Beofre using the remainder of section 1.b of this notebook, you will have to use <a href="https://github.com/elceef/dnstwist">dnstwist instructions</a> (outside of this notebook) to create a domains_dnstwists.csv. In our example (below) we generated variations for google.com using dnstwist. You can automate this for your own organization or for any organization of interest. 
# MAGIC 
# MAGIC After intalling dnstwist, we ran:<br/>
# MAGIC <code>
# MAGIC &nbsp;  
# MAGIC dnstwist --registered google.com >> domains_dnstwists.csv<br/>
# MAGIC addition       googlea.com    184.168.131.241 NS:ns65.domaincontrol.com MX:mailstore1.secureserver.net<br/>
# MAGIC addition       googleb.com    47.254.33.193 NS:ns3.dns.com </code>
# MAGIC 
# MAGIC We formatted domains_dnstwists.csv with a header: PERMUTATIONTYPE,domain,meta
# MAGIC 
# MAGIC Once you have created domain_dnstwists.csv, you can continue:
# MAGIC - load the dnstwisted domains
# MAGIC - enrich the table with domain names (without TLDs)
# MAGIC - load the dnstwist enriched results into a silver table
# MAGIC 
# MAGIC We will use these tables later to productionize typosquatting detection.

# COMMAND ----------

# MAGIC %python
# MAGIC #NOTE: domain_dnstwists.csv needs to be created outside of this notebook, using instructions from dnstwist. 
# MAGIC #Load the domain_dnstwists.csv into a dataframe, brand_domains_monitored_raw. Note the csv and header, true options.
# MAGIC brand_domains_monitored_raw = spark.read.format("csv").option("header", "true").load("dbfs:/FileStore/tables/datasets/domains_dnstwists.csv") 

# COMMAND ----------

#Display csv we just read
display(brand_domains_monitored_raw)

# COMMAND ----------

#Load the csv brand_domains_monitored_raw into a local table called, brand_domains_monitored_raw
brand_domains_monitored_raw.createOrReplaceTempView("brand_domains_monitored_raw")

# COMMAND ----------

#Extract the domain names using the UDF we created at Cmd 9 of this notebook.
#Create a new table with the dnstwist extracted domains. New column dnstwisted_domain
#The hardcoded ">=2" is there to accomodate for potential empty domain fileds
brand_domains_monitored_enriched = spark.sql("select *, domain_extract(domain) as dnstwisted_domain  from brand_domains_monitored_raw").filter("char_length(dnstwisted_domain) >= 2")
display(brand_domains_monitored_enriched)

# COMMAND ----------

#Define a silver, Delta table
brand_domains_monitored_enriched.write.format("delta").mode('overwrite').option("mergeSchema", "false").save("dbfs:/FileStore/tables/datasets/tables/silver/delta/brand_domains_monitored_enriched")

# COMMAND ----------

# MAGIC %sql
# MAGIC /*Create the silver, Delta table with the enriched data*/
# MAGIC CREATE DATABASE IF NOT EXISTS silver;
# MAGIC DROP TABLE IF EXISTS silver.EnrichedTwistedDomainBrand;
# MAGIC CREATE TABLE IF NOT EXISTS silver.EnrichedTwistedDomainBrand 
# MAGIC USING DELTA LOCATION 'dbfs:/FileStore/tables/datasets/tables/silver/delta/brand_domains_monitored_enriched'

# COMMAND ----------

# MAGIC %sql
# MAGIC /*Query the silver Delta table*/
# MAGIC Select *  from silver.EnrichedTwistedDomainBrand

# COMMAND ----------

# MAGIC %md
# MAGIC # 2. Loading the data
# MAGIC We admit, that felt like a lot of work to prep URLHaus and dnstwist. But we are now ready for typosquatting detection and threat intel enrichment. 
# MAGIC 
# MAGIC Now, we can load the pDNS data into a <b>Bronze</b>, Delta table.  We will then enrich the data with tldextract, GeoIP lookups, a DGA Classifier, URLHaus, threat intel lookups.
# MAGIC We will do this using spark SQL.

# COMMAND ----------

# MAGIC %sql
# MAGIC /*We create a new bronze table and load the pDNS data we saved in an earlier step */
# MAGIC CREATE DATABASE IF NOT EXISTS bronze;
# MAGIC DROP TABLE IF EXISTS bronze.DNS;
# MAGIC CREATE TABLE IF NOT EXISTS bronze.DNS 
# MAGIC USING DELTA LOCATION 'dbfs:/FileStore/tables/tables/bronze/delta/DNS_raw'

# COMMAND ----------

# MAGIC %sql
# MAGIC /*We check to see how many records we loaded*/
# MAGIC SELECT count(*) from bronze.DNS

# COMMAND ----------

#Create user defined functions (UDF) for loading and manipulating the Geo data 
#The code here will perform Geo-IP lookups using the ip address available in the rdata field in our bronze table
# We use a free geo database from Maxmind: https://dev.maxmind.com/geoip/geoip2/geolite2/ 
import geoip2.database
import geoip2.errors
from pyspark.sql.functions import lit

from geoip2 import database
from pyspark import SparkContext, SparkFiles

#You can download this database from: https://dev.maxmind.com/geoip/geoip2/geolite2/ 
#You can upload the GeoLite2_City database file by using the databricks UI. 
#Databricks Navigator (lefthand bar) -> Data -> Upload File -> Select 
#Note if you receive an error here,  
city_db = '/databricks/driver/data/GeoLite2_City.mmdb'

def get_country_code(ip):
    if ip is None:
      return None
    
    geocity = database.Reader(SparkFiles.get(city_db))
    try:
      record = geocity.city(ip)
      return record.country.iso_code
    except geoip2.errors.AddressNotFoundError:
      return None
    
def get_country(ip):
    if ip is None:
      return None
    
    geocity = database.Reader(SparkFiles.get(city_db))
    try:
      record = geocity.city(ip)
      return record.country.name
    except geoip2.errors.AddressNotFoundError:
      return None

def get_city(ip):
    if ip is None:
      return None
    
    geocity = database.Reader(SparkFiles.get(city_db))
    try:
      record = geocity.city(ip)
      return record.city.name
    except geoip2.errors.AddressNotFoundError:
      return None
 

spark.udf.register("get_city", get_city)
spark.udf.register("get_country", get_country)
spark.udf.register("get_country_code", get_country_code)

# COMMAND ----------

#Load the DGA model. This is a pre-trained model that we will use to enrich our incoming DNS events. You will see how to train this model in a later step.
import mlflow
import mlflow.pyfunc

model_path = 'file:///databricks/driver/model'
loaded_model = mlflow.pyfunc.load_model(model_path)
spark.udf.register("ioc_detect", loaded_model.predict)

# COMMAND ----------

dbutils.fs.ls("/FileStore/tables/model")

# COMMAND ----------

#Filtering on the rrtype of A 
dns_table = spark.table("bronze.DNS").selectExpr("*", "case when rrtype = 'A' then element_at(rdata, 1) else null end as ip_address ")

# COMMAND ----------

#Enrich the data with city, country, country codes, ioc and domain name
dns_table_enriched = dns_table.selectExpr("*", "case when ip_address is not null then get_country(ip_address) else null end as country", 
                     "case when ip_address is not null then get_city(ip_address) else null end as city", 
                     "case when ip_address is not null then get_country_code(ip_address) else null end as country_code", 
                     "case when char_length(domain_extract(rrname)) > 5 then ioc_detect(string(domain_extract(rrname))) else null end as ioc",
                     " domain_extract(rrname) as domain_name")

# COMMAND ----------

#Persist the enriched DNS data
dns_table_enriched.write.format("delta").mode('overwrite').option("mergeSchema", "true").save("dbfs:/FileStore/tables/tables/silver/delta/DNS")

# COMMAND ----------

# MAGIC %sql
# MAGIC --Create a Delta table from the enriched dns data. This is the table that will be used by the DGA analytics later.
# MAGIC DROP TABLE IF EXISTS silver.DNS;
# MAGIC CREATE TABLE IF NOT EXISTS silver.DNS 
# MAGIC USING DELTA LOCATION 'dbfs:/FileStore/tables/tables/silver/delta/DNS'

# COMMAND ----------

# MAGIC %sql
# MAGIC /*We load the enriched threat intel into a silver table
# MAGIC Create a Delta table from the enriched dns data. This is the table that will be used by the DGA analytics later.*/
# MAGIC DROP TABLE IF EXISTS silver.EnrichedThreatFeeds;
# MAGIC CREATE TABLE IF NOT EXISTS silver.EnrichedThreatFeeds 
# MAGIC USING DELTA LOCATION 'dbfs:/FileStore/tables/tables/silver/delta/enriched_threat_feeds'

# COMMAND ----------

# MAGIC %sql
# MAGIC /*We check to see how many records we have loaded*/
# MAGIC Select count(*) from silver.EnrichedThreatFeeds

# COMMAND ----------

# MAGIC %md
# MAGIC # 3. Ad-Hoc Analytics: Exploring the data
# MAGIC FINALLY!!!! We have data. And we can start poking around. This is an optional section for you to familiarize yourself with the data. And pick up some spark SQL tricks. You can use these tactics to explore and expand on the analytics.

# COMMAND ----------

# MAGIC %sql 
# MAGIC -- Lets take a look at the number of unique domains in our dataset 
# MAGIC select count(distinct(domain_name)) from silver.dns

# COMMAND ----------

# MAGIC %sql select count(*) from silver.dns

# COMMAND ----------

# MAGIC %sql
# MAGIC -- ioc is a field we've created as a result of running the DGA model. If the ioc field has a value of ioc, it means that the DGA model has determeined the domain to be an ioc (indicator of compromise)
# MAGIC -- The query below is for a total count of rows where the DGA algorithm has detected an ioc. But excludes an domains that have the string 'ip' in it and has a domain name length of more than 8 characters
# MAGIC select count(*), domain_name, country from silver.dns where ioc = 'ioc' and domain_name not like '%ip%' and char_length(domain_name) > 8 group by domain_name, country order by count(*) desc

# COMMAND ----------

# MAGIC %md
# MAGIC Let us check against the known threat feeds

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Query for domains in the silver.dns, silver.EnrichedThreatFeeds tables where there is an ioc match.
# MAGIC -- You may have experienced: many to many match/join is compute cost prohibitive in most SIEM/log aggregation systems. Spark SQL is a lot more efficient. 
# MAGIC select count(distinct(domain_name))
# MAGIC from silver.dns, silver.EnrichedThreatFeeds where silver.dns.domain_name == silver.EnrichedThreatFeeds.domain

# COMMAND ----------

# MAGIC %sql 
# MAGIC -- Query for ioc matches across multiple tables. Similar to previous example but with additional columns in the results table
# MAGIC select  domain_name, rrname, country, time_first, time_last, ioc,rrtype,rdata,bailiwick, silver.EnrichedThreatFeeds.* 
# MAGIC from silver.dns, silver.EnrichedThreatFeeds where silver.dns.domain_name == silver.EnrichedThreatFeeds.domain and ioc='ioc'

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Looking for specific rrnames in multiple tables.
# MAGIC select  domain_name, rrname, country, time_first, time_last, ioc,rrtype,rdata,bailiwick, silver.EnrichedThreatFeeds.* 
# MAGIC from silver.dns, silver.EnrichedThreatFeeds where silver.dns.domain_name == silver.EnrichedThreatFeeds.domain  and silver.dns.rrname = "ns1.asdklgb.cf." OR silver.dns.rrname LIKE "%cn."

# COMMAND ----------

# MAGIC %md
# MAGIC # 4. ML Training and Analytics
# MAGIC In this section we will build a simple DGA model and the typosquatting model. Slides below have some high level discussion on DGA.  
# MAGIC - A detailed discussion on DGA is [here:](http://www.covert.io/getting-started-with-dga-research/)
# MAGIC - In this example we only implement one DGA model, but multiple DGA models can be implemented in our enrichment pipeline, some examples of other DGA models trained with Neural Networks are available [here:](http://www.covert.io/auxiliary-loss-optimization-for-hypothesis-augmentation-for-dga-domain-detection/)
# MAGIC - A more detailed discussion on typosquatting is [here:](https://www.mcafee.com/blogs/consumer/what-is-typosquatting/)
# MAGIC 
# MAGIC At a high level we will:
# MAGIC - Extract the domain names from the data removing gTLD (e.g. .com, .org) and ccTLD (e.g. .ru, cn, .uk, .ca)
# MAGIC - Build the models 

# COMMAND ----------

# MAGIC %scala
# MAGIC displayHTML("""<iframe src="https://docs.google.com/presentation/d/e/2PACX-1vRqDKRAKkXWhcRavKMvJE1BKzpoI4UvofIFQdIpoTV1d7Z3b4XdIsRt6O0iAFV8waBPvrMLVUdHFcND/embed?start=false&loop=false&delayms=3000" frameborder="0" width="960" height="569" allowfullscreen="true" mozallowfullscreen="true" webkitallowfullscreen="true"></iframe>
# MAGIC """)

# COMMAND ----------

#Read the Alexa list of domains
#Alexa is a list of the most popular domains on the internet ranked by popularity
#Alexa is not intended as a whitelist. 
import pandas as pd
import mlflow
import mlflow.sklearn
import mlflow.pyfunc
alexa_dataframe = pd.read_csv(default_file_path + "/alexa_100k.txt");
display(alexa_dataframe)

# COMMAND ----------

#Extract the domains names without gTLD or ccTLD (generic or country code top-level domain) from the registered domain and subdomains of a URL.
#We only need the domain names for training.
#Example fields in a tldextract result: ExtractResult(subdomain='forums.news', domain='cnn', suffix='com')
import tldextract
import numpy as np
def domain_extract(uri):
    ext = tldextract.extract(uri)
    if (not ext.suffix):
        return np.nan
    else:
        return ext.domain

spark.udf.register("domain_extract", domain_extract)

alexa_dataframe['domain'] = [ domain_extract(uri) for uri in alexa_dataframe['uri']]
del alexa_dataframe['uri']
del alexa_dataframe['rank']
display(alexa_dataframe)

# COMMAND ----------

#Add legitimate domains from Alexa to the training data
# It's possible we have NaNs from blanklines or whatever
alexa_dataframe = alexa_dataframe.dropna()
alexa_dataframe = alexa_dataframe.drop_duplicates()

# Set the class
alexa_dataframe['class'] = 'legit'

# Shuffle the data (important for training/testing)
alexa_dataframe = alexa_dataframe.reindex(np.random.permutation(alexa_dataframe.index))
alexa_total = alexa_dataframe.shape[0]
print('Total Alexa domains %d' % alexa_total)
display(alexa_dataframe)

# COMMAND ----------


file_location = default_file_path + "/dga_domains_header.txt"

dga_dataframe = pd.read_csv(file_location, header=0);
# We noticed that the blacklist values just differ by captilization or .com/.org/.info
dga_dataframe['domain'] = dga_dataframe.applymap(lambda x: x.split('.')[0].strip().lower())

# It's possible we have NaNs from blanklines or whatever
dga_dataframe = dga_dataframe.dropna()
dga_dataframe = dga_dataframe.drop_duplicates()
dga_total = dga_dataframe.shape[0]
print('Total DGA domains %d' % dga_total)

# Set the class
dga_dataframe['class'] = 'ioc'

print('Number of DGA domains: %d' % dga_dataframe.shape[0])
all_domains = pd.concat([alexa_dataframe, dga_dataframe], ignore_index=True)

# COMMAND ----------

#Output of DGA detections from our dataset
display(dga_dataframe)

# COMMAND ----------

#Lets do some feature engineering and add calculations for entropy and length to our dataset.
#We calculate entropy by comparing the number of unique characters in our string to its length.
all_domains['length'] = [len(x) for x in all_domains['domain']]
all_domains = all_domains[all_domains['length'] > 6]

import math
from collections import Counter
 
def entropy(s):
    p, lns = Counter(s), float(len(s))
    return -sum( count/lns * math.log(count/lns, 2) for count in p.values())
  
all_domains['entropy'] = [entropy(x) for x in all_domains['domain']]

# COMMAND ----------

#Print the results. The higher the entropy the higher the potential for DGA. But we aren't done quite yet.
display(all_domains)

# COMMAND ----------

#Here we do additional feature engineering to do n-gram frequency analysis our valid domains

y = np.array(all_domains['class'].tolist()) # Yes, this is weird but it needs 

import sklearn.ensemble
from sklearn import feature_extraction


alexa_vc = sklearn.feature_extraction.text.CountVectorizer(analyzer='char', ngram_range=(3,5), min_df=1e-4, max_df=1.0)
counts_matrix = alexa_vc.fit_transform(alexa_dataframe['domain'])
alexa_counts = np.log10(counts_matrix.sum(axis=0).getA1())
ngrams_list = alexa_vc.get_feature_names()

counts_matrix = alexa_vc.fit_transform(alexa_dataframe['domain'])
alexa_counts = np.log10(counts_matrix.sum(axis=0).getA1())
ngrams_list = alexa_vc.get_feature_names()

# COMMAND ----------

#Load dictionary words into a dataframe
file_location = default_file_path + "/words.txt"
word_dataframe = pd.read_csv(file_location, header=0, sep=';');
word_dataframe = word_dataframe[word_dataframe['words'].map(lambda x: str(x).isalpha())]
word_dataframe = word_dataframe.applymap(lambda x: str(x).strip().lower())
word_dataframe = word_dataframe.dropna()
word_dataframe = word_dataframe.drop_duplicates()

# COMMAND ----------

#Create a dictionary from the word list
dict_vc = sklearn.feature_extraction.text.CountVectorizer(analyzer='char', ngram_range=(3,5), min_df=1e-5, max_df=1.0)
counts_matrix = dict_vc.fit_transform(word_dataframe['words'])
dict_counts = np.log10(counts_matrix.sum(axis=0).getA1())
ngrams_list = dict_vc.get_feature_names()

def ngram_count(domain):
    alexa_match = alexa_counts * alexa_vc.transform([domain]).T  # Woot vector multiply and transpose Woo Hoo!
    dict_match = dict_counts * dict_vc.transform([domain]).T
    print('%s Alexa match:%d Dict match: %d' % (domain, alexa_match, dict_match))

# Examples:
ngram_count('beyonce')
ngram_count('dominos')
ngram_count('1cb8a5f36f')
ngram_count('zfjknuh38231')
ngram_count('bey6o4ce')
ngram_count('washington')

# COMMAND ----------

#Create n-grams from the dictionary and Alex 100k list. And build a matching function. And run test examples.
#More on ngrams here: https://blog.xrds.acm.org/2017/10/introduction-n-grams-need/ 

all_domains['alexa_grams']= alexa_counts * alexa_vc.transform(all_domains['domain']).T 
all_domains['word_grams']= dict_counts * dict_vc.transform(all_domains['domain']).T 

# COMMAND ----------

# MAGIC %md
# MAGIC **#Build a vectorized model of the n-grams. We need vectors for building the model.  **

# COMMAND ----------

weird_cond = (all_domains['class']=='legit') & (all_domains['word_grams']<3) & (all_domains['alexa_grams']<2)
weird = all_domains[weird_cond]
print(weird.shape[0])
all_domains.loc[weird_cond, 'class'] = 'weird'
print(all_domains['class'].value_counts())

# COMMAND ----------

# MAGIC %md
# MAGIC ** Let's train our model **

# COMMAND ----------

#Labelling the domains based on weirdness 
# Using ML runtime, my packages come pre-installed
# Using ML flow, we can track our expirements as we iterate

from sklearn.model_selection import train_test_split
clf = sklearn.ensemble.RandomForestClassifier(n_estimators=20) # Trees in the forest

not_weird = all_domains[all_domains['class'] != 'weird']
X = not_weird[['length', 'entropy', 'alexa_grams', 'word_grams']].values

# Labels (scikit learn uses 'y' for classification labels)
y = np.array(not_weird['class'].tolist())

with mlflow.start_run():
  # Train on a 80/20 split
  X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
  clf.fit(X_train, y_train)
  y_pred = clf.predict(X_test)
  # First train on the whole thing before looking at prediction performance
  clf.fit(X, y)


# COMMAND ----------

#Locate the model in our content library using MLFlow
run_id = mlflow.search_runs()['run_id'][0]
model_uri = 'runs:/' + run_id + '/model'

# COMMAND ----------

#Get the run id of this model-run
run_id

# COMMAND ----------

# Build a predict function to be used later to do DGA predictions
# Add in pre and post processing for our predict function

import mlflow.pyfunc

class vc_transform(mlflow.pyfunc.PythonModel):
    def __init__(self, alexa_vc, dict_vc, ctx):
        self.alexa_vc = alexa_vc
        self.dict_vc = dict_vc
        self.ctx = ctx

    def predict(self, context, model_input):
        _alexa_match = alexa_counts * self.alexa_vc.transform([model_input]).T  
        _dict_match = dict_counts * self.dict_vc.transform([model_input]).T
        _X = [len(model_input), entropy(model_input), _alexa_match, _dict_match]
        return str(self.ctx.predict([_X])[0])

# COMMAND ----------

# Save our predict function
# NOTE - known bug: This command will only execute once - you can ignore errors when running it twice
from mlflow.exceptions import MlflowException
model_path = 'dbfs:/FileStore/tables/new_model/dga_model'

vc_model = vc_transform(alexa_vc, dict_vc, clf)
mlflow.pyfunc.save_model(path=model_path.replace("dbfs:", "/dbfs"), python_model=vc_model)

# COMMAND ----------

#Save the trained model 
vc_model = vc_transform(alexa_vc, dict_vc, clf)
vc_model.predict(mlflow.pyfunc.PythonModel, '7ydbdehaaz')

# COMMAND ----------

# MAGIC %md
# MAGIC # 6. Near Realtime Streaming Analytics
# MAGIC Enrich data with threat intel and Detect malicious activity using the analytics and enrichments 

# COMMAND ----------

#Defining the schema for pDNS. 
from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType, LongType, StringType, ArrayType

schema = StructType([
    StructField("rrname", StringType(), True),
    StructField("rrtype", StringType(), True),
    StructField("time_first", LongType(), True),
    StructField("time_last", LongType(), True),
    StructField("count", LongType(), True),
    StructField("bailiwick", StringType(), True),
    StructField("rdata", ArrayType(StringType(), True), True)
])


# COMMAND ----------

#Load test data set
df=spark.readStream.format("json").schema(schema).load(f"dbfs:/FileStore/tables/datasets/latest")

# COMMAND ----------

#Create a temporary table of the test dataset 
df.createOrReplaceTempView("dnslatest")

# COMMAND ----------

#Visual inspection. You can see that row 1 and 3 are suspect.
display(df)

# COMMAND ----------

# MAGIC %sql
# MAGIC --This is where we do the DGA detection
# MAGIC --Create a view to score the dns data
# MAGIC 
# MAGIC CREATE OR REPLACE TEMPORARY VIEW  dns_latest_new
# MAGIC AS SELECT rdata, count, rrname, bailiwick, rrtype, time_last, time_first, ioc_detect(domain_extract(rrname)) as isioc, domain_extract(dnslatest.rrname) domain  from dnslatest

# COMMAND ----------

# MAGIC %md
# MAGIC ##6.1 Find threats in DNS Event Stream

# COMMAND ----------

# MAGIC %sql
# MAGIC 
# MAGIC Select * from dns_latest_new  where isioc = 'ioc'

# COMMAND ----------

# MAGIC %sql
# MAGIC --Phishing or Typosquating?
# MAGIC --This is where we do typosquatting detection
# MAGIC --By using dnstwist, we find the suspicious domain, googlee
# MAGIC Select silver.EnrichedTwistedDomainBrand.*  FROM dns_latest_new, silver.EnrichedTwistedDomainBrand Where silver.EnrichedTwistedDomainBrand.dnstwisted_domain = dns_latest_new.domain

# COMMAND ----------

#The next few lines we will be applying our models:
# - To detect the bad domains
# - Create an alerts table
dns_stream_iocs = spark.sql("Select * from dns_latest_new  where isioc = 'ioc'")
dbutils.fs.rm('dbfs:/tmp/datasets/gold/delta/DNS_IOC_Latest', True)
dns_stream_iocs.writeStream.format("delta").outputMode("append").option("checkpointLocation", "dbfs:/tmp/datasets/gold/delta/_checkpoints/DNS_IOC_Latest").start("dbfs:/tmp/datasets/gold/delta/DNS_IOC_Latest")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Agent Tesla
# MAGIC Success!!! 
# MAGIC - We used the DGA detection model on streaming DNS events, 
# MAGIC - Identified a supsicious domain (ioc) in our DNS logs, 
# MAGIC - Enriched the ioc with URLHaus
# MAGIC - We can we can see that it this DGA domain is serving up agent tesla

# COMMAND ----------

# MAGIC %sql
# MAGIC --We found the bad domain - lets see if our enriched threat feeds have intel on this domain? 
# MAGIC select * from silver.EnrichedThreatFeeds where  silver.EnrichedThreatFeeds.domain = domain_extract('ns1.asdklgb.cf.')
