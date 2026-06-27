import re

f = open('app/web_app.py', 'r', encoding='utf-8')
c = f.read()
f.close()

new_routes = '''
    @app.route('/retailer-download')
    def retailer_download_page():
        db_path = _db_path()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            distributors = [dict(r) for r in conn.execute('SELECT id, firm_name, firm_nick_name FROM master_distributors ORDER BY firm_name').fetchall()]
        return render_template_string(RETAILER_DOWNLOAD_TEMPLATE, distributors=distributors)

    @app.route('/retailer-download/excel')
    def retailer_download_excel():
        db_path = _db_path()
        dist_id = request.args.get('dist_id', 'all')
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            if dist_id == 'all':
                rows = conn.execute("""
                    SELECT r.id, r.retailer_code, r.name as retailer_name,
                    d.firm_name as distributor_name, d.firm_nick_name,
                    r.location, r.phone_number, r.email, r.address, r.gst_no
                    FROM master_retailers r
                    LEFT JOIN master_distributors d ON r.distributor_id = d.id
                    ORDER BY d.firm_name, r.name
                """).fetchall()
                filename = 'all_retailers.xlsx'
            else:
                rows = conn.execute("""
                    SELECT r.id, r.retailer_code, r.name as retailer_name,
                    d.firm_name as distributor_name, d.firm_nick_name,
                    r.location, r.phone_number, r.email, r.address, r.gst_no
                    FROM master_retailers r
                    LEFT JOIN master_distributors d ON r.distributor_id = d.id
                    WHERE r.distributor_id = ?
                    ORDER BY r.name
                """, (dist_id,)).fetchall()
                dist_name = rows[0]['firm_nick_name'] if rows else str(dist_id)
                filename = f'{dist_name}_retailers.xlsx'
        import io as _io
        import pandas as _pd
        df = _pd.DataFrame([dict(r) for r in rows])
        output = _io.BytesIO()
        with _pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Retailers')
        output.seek(0)
        return Response(
            output.read(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    @app.route('/retailer-download/csv')
    def retailer_download_csv():
        import csv as _csv
        from io import StringIO as _StringIO
        db_path = _db_path()
        dist_id = request.args.get('dist_id', 'all')
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            if dist_id == 'all':
                rows = conn.execute("""
                    SELECT r.id, r.retailer_code, r.name as retailer_name,
                    d.firm_name as distributor_name, d.firm_nick_name,
                    r.location, r.phone_number, r.email, r.address, r.gst_no
                    FROM master_retailers r
                    LEFT JOIN master_distributors d ON r.distributor_id = d.id
                    ORDER BY d.firm_name, r.name
                """).fetchall()
                filename = 'all_retailers.csv'
            else:
                rows = conn.execute("""
                    SELECT r.id, r.retailer_code, r.name as retailer_name,
                    d.firm_name as distributor_name, d.firm_nick_name,
                    r.location, r.phone_number, r.email, r.address, r.gst_no
                    FROM master_retailers r
                    LEFT JOIN master_distributors d ON r.distributor_id = d.id
                    WHERE r.distributor_id = ?
                    ORDER BY r.name
                """, (dist_id,)).fetchall()
                dist_name = rows[0]['firm_nick_name'] if rows else str(dist_id)
                filename = f'{dist_name}_retailers.csv'
        output = _StringIO()
        if rows:
            writer = _csv.DictWriter(output, fieldnames=list(dict(rows[0]).keys()))
            writer.writeheader()
            writer.writerows([dict(r) for r in rows])
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

'''

new_template = '''
RETAILER_DOWNLOAD_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Retailer Download</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    .card { border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 8px; }
    .btn { display: inline-block; padding: 0.5rem 1.2rem; border-radius: 4px; text-decoration: none; margin: 0.3rem; font-size: 0.9rem; }
    .btn-excel { background: #1d6f42; color: white; }
    .btn-csv { background: #0d6efd; color: white; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background: #f0f0f0; }
  </style>
</head>
<body>
  <h1>📥 Retailer Download</h1>

  <div class="card">
    <h2>All Retailers Download</h2>
    <p>Total 750 retailers — sabhi distributors ke saath</p>
    <a href="/retailer-download/excel?dist_id=all" class="btn btn-excel">📊 Excel Download (All)</a>
    <a href="/retailer-download/csv?dist_id=all" class="btn btn-csv">📄 CSV Download (All)</a>
  </div>

  <div class="card">
    <h2>Distributor Wise Download</h2>
    <table>
      <thead>
        <tr><th>Distributor</th><th>Nick Name</th><th>Excel</th><th>CSV</th></tr>
      </thead>
      <tbody>
        {% for d in distributors %}
        <tr>
          <td>{{ d.firm_name }}</td>
          <td>{{ d.firm_nick_name }}</td>
          <td><a href="/retailer-download/excel?dist_id={{ d.id }}" class="btn btn-excel">📊 Excel</a></td>
          <td><a href="/retailer-download/csv?dist_id={{ d.id }}" class="btn btn-csv">📄 CSV</a></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <p><a href="/">← Back</a> | <a href="/analytics">Analytics</a></p>
</body>
</html>
"""

'''

# Add routes before return app
c = c.replace('    return app', new_routes + '    return app', 1)

# Add template before REPORTS_TEMPLATE
c = c.replace('REPORTS_TEMPLATE = """', new_template + 'REPORTS_TEMPLATE = """', 1)

f = open('app/web_app.py', 'w', encoding='utf-8')
f.write(c)
f.close()
print('Done! Routes and template added.')
