
Initial Prompt:

~~~
summarize what's in the yahoo finance news rss feed
~~~
The code it generated didn't bring in the text of the article just the metadata.

~~~
how can I get the article text from the yahoo finance news rss feed.  The feed is at https://finance.yahoo.com/news/rssindex
~~~
The code from this itteration worked but the output was in xml format - yuck
~~~
re-write the code so it outputs json rather than xml
~~~

Ideas for future itterations:
👉 Do you also want me to add per-article JSON files (e.g., one JSON per article in a folder) for easier incremental processing, or keep it all in one big JSON file?
