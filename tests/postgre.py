import psycopg2

conn = psycopg2.connect(
    host="localhost",
    port=5432,
    user="postgres",
    password="password",
    dbname="tradebot"
)
cur = conn.cursor()
cur.execute("SELECT version();")
print(cur.fetchone())
conn.close()