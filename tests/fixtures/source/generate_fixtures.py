#!/usr/bin/env python3
"""Generate fixture OKF bundles from the Chinook database.

Simulates two SORs:
  MUSICSTORE_CORE  (engine: ansi)     - catalog tables, declared PK/FK/indexes kept
  MUSICSTORE_SALES (engine: teradata) - sales tables, constraints STRIPPED (legacy
                                        no-FK simulation), simulated Primary Indexes
Profiles are measured from real data. Fingerprints are real hashes.
The relationship bundle's Jaccard numbers are actually computed.
"""
import sqlite3, re, json, hashlib, difflib, os
from collections import Counter
from datetime import date

DB = sqlite3.connect('/home/claude/chinook.db')
OUT = '/home/claude/fixtures/okf'
TODAY = date.today().isoformat()

CORE_TABLES = ['artist', 'album', 'track', 'genre', 'media_type', 'playlist', 'playlist_track']
SALES_TABLES = ['customer', 'employee', 'invoice', 'invoice_line']

BUNDLES = {
    'MUSICSTORE_CORE':  {'engine': 'ansi', 'schema': 'CORE',  'tables': CORE_TABLES,  'keep_constraints': True, 'keep_indexes': True},
    'MUSICSTORE_SALES': {'engine': 'teradata', 'schema': 'SALES', 'tables': SALES_TABLES, 'keep_constraints': False, 'keep_indexes': True},
}

# Simulated Teradata Primary Indexes for SALES (legacy designers' join intent)
SALES_PI = {'customer': 'customer_id', 'employee': 'employee_id',
            'invoice': 'invoice_id', 'invoice_line': 'invoice_id'}

# Simulated compliance config: sensitive columns (no top-N, no fingerprints)
SENSITIVE = {('customer', c) for c in ['email', 'phone', 'fax', 'address', 'postal_code']} | \
            {('employee', c) for c in ['email', 'phone', 'fax', 'address', 'postal_code', 'birth_date']}

TOPN_GATE = 1000
FP_RATIO_GATE = 0.5
FP_SAMPLE = 5000

# ---------- parse DDL for types, PKs; declared FK/idx metadata ----------
sql_src = open('/mnt/project/Chinook_PostgreSql.sql').read()
meta = json.load(open('/home/claude/declared_meta.json'))
FKS = meta['fks']      # (table, constraint, col, ref_table, ref_col)
IDXS = meta['indexes'] # (index_name, table, col)

col_types, pks = {}, {}
for m in re.finditer(r'CREATE TABLE (\w+)\s*\((.*?)\);', sql_src, re.DOTALL):
    tname, body = m.group(1), m.group(2)
    for line in body.split('\n'):
        line = line.strip().rstrip(',')
        pk = re.match(r'CONSTRAINT \w+ PRIMARY KEY\s*\(([^)]+)\)', line)
        if pk:
            pks[tname] = [c.strip() for c in pk.group(1).split(',')]
            continue
        cm = re.match(r'(\w+)\s+(VARCHAR\(\d+\)|NUMERIC\(\d+,\d+\)|INT|TIMESTAMP|DATE)\s*(NOT NULL)?', line)
        if cm:
            col_types[(tname, cm.group(1))] = (cm.group(2), cm.group(3) is None)

def columns_of(t):
    return [c for (tt, c) in col_types if tt == t]

# ---------- profiling ----------
def fmt_pattern(vals):
    pats = Counter()
    for v in vals[:500]:
        s = str(v)
        if re.fullmatch(r'\d+', s): pats['all-digits'] += 1
        elif re.fullmatch(r'\d{4}-\d{2}-\d{2}.*', s): pats['iso-datetime'] += 1
        elif re.fullmatch(r'[^@]+@[^@]+\.[^@]+', s): pats['email'] += 1
        elif re.fullmatch(r'[+\d\s()\-]+', s): pats['phone-like'] += 1
        elif re.fullmatch(r'[A-Za-z .\'-]+', s): pats['alpha'] += 1
        else: pats['mixed'] += 1
    return pats.most_common(1)[0][0] if pats else 'empty'

def normalize(v):
    rules = []
    s = str(v).strip()
    if s != str(v): rules.append('trim')
    u = s.upper()
    if u != s: rules.append('uppercase')
    if re.fullmatch(r'\d+', u) and len(u) > 1 and u[0] == '0':
        u = u.lstrip('0') or '0'; rules.append('strip-leading-zeros')
    return u, rules

def profile(table, col):
    cur = DB.execute(f'SELECT "{col}" FROM "{table}"')
    vals = [r[0] for r in cur.fetchall()]
    total = len(vals)
    nn = [v for v in vals if v is not None]
    distinct = set(nn)
    p = {
        'total_rows': total, 'non_null': len(nn), 'distinct_count': len(distinct),
        'null_rate': round(1 - len(nn)/total, 4) if total else 0,
        'distinct_ratio': round(len(distinct)/len(nn), 4) if nn else 0,
        'min': min(nn) if nn else None, 'max': max(nn) if nn else None,
        'format': fmt_pattern(nn),
    }
    if nn and all(isinstance(v, int) for v in nn) and p['distinct_count'] > 1:
        span = p['max'] - p['min'] + 1
        p['dense_sequence'] = p['min'] <= 2 and p['distinct_count'] / span >= 0.95
    else:
        p['dense_sequence'] = False
    if nn and isinstance(nn[0], str):
        lens = [len(v) for v in nn]
        p['len'] = (min(lens), max(lens), round(sum(lens)/len(lens), 1))
    if len(distinct) < TOPN_GATE:
        p['top'] = Counter(nn).most_common(20)
    return p, distinct

def fingerprint(distinct_vals):
    norm_rules = Counter()
    hashes = set()
    for v in list(distinct_vals)[:FP_SAMPLE]:
        nv, rules = normalize(v)
        for r in rules: norm_rules[r] += 1
        hashes.add(hashlib.sha256(nv.encode()).hexdigest()[:16])
    applied = sorted([r for r, c in norm_rules.items() if c > 0])
    return sorted(hashes), applied

# ---------- descriptions (simulated annotator output) ----------
TABLE_DESC = {
 'artist': ('Recording artists master list', 'high',
   'Master list of recording artists. One row per artist; referenced by album.'),
 'album': ('Albums with owning artist reference', 'high',
   'Album catalog. Each album belongs to one artist via artist_id.'),
 'track': ('Track catalog: album, genre, media type, duration, price', 'high',
   'Central track catalog. Carries album/genre/media type references, playback length, file size, and unit price.'),
 'genre': ('Music genre reference codes', 'high', 'Small reference list of music genres.'),
 'media_type': ('Media format reference codes', 'high', 'Reference list of media/file formats.'),
 'playlist': ('Named playlists', 'high', 'User-facing named playlists.'),
 'playlist_track': ('Playlist-to-track assignment (bridge)', 'high',
   'Bridge table assigning tracks to playlists. Composite key (playlist_id, track_id).'),
 'customer': ('Customer master with contact data and support rep', 'high',
   'Customer master. Contact/address fields plus assigned support representative (support_rep_id).'),
 'employee': ('Employee master with reporting hierarchy', 'high',
   'Employee master including self-referencing reports_to hierarchy.'),
 'invoice': ('Invoice headers: customer, date, billing, total', 'high',
   'Invoice header per purchase. Customer reference, invoice date, billing address snapshot, total amount.'),
 'invoice_line': ('Invoice line items: track sold, price, quantity', 'high',
   'Invoice detail lines. Each line references the sold track (track_id) and its price/quantity. NOTE: track data lives in the CORE database - cross-SOR reference.'),
}
def col_desc(t, c, p):
    known = {
      ('track','unit_price'): ('Sale price per track; two price points observed.', 'high'),
      ('invoice_line','track_id'): ('Track sold on this line. Values match CORE track catalog identifiers.', 'medium'),
      ('invoice','total'): ('Invoice total amount (sum of line extensions).', 'high'),
      ('customer','support_rep_id'): ('Assigned support employee; joins to employee.', 'high'),
      ('employee','reports_to'): ('Manager employee_id (self-reference); null for top of hierarchy.', 'high'),
      ('track','milliseconds'): ('Track duration in milliseconds.', 'high'),
      ('track','bytes'): ('Media file size in bytes.', 'medium'),
    }
    if (t, c) in known: return known[(t, c)]
    if c.endswith('_id') and p['distinct_ratio'] > 0.99 and p['null_rate'] == 0:
        return (f'Unique identifier; candidate key of {t}.', 'high')
    if c.endswith('_id'):
        return (f'Reference identifier ({c[:-3]}).', 'medium')
    if c in ('name', 'title'): return (f'Display name of the {t} record.', 'high')
    if 'date' in c: return ('Date attribute; ISO datetime format observed.', 'high')
    if p['format'] == 'email': return ('Email address.', 'high')
    if p['format'] == 'phone-like': return ('Phone/fax number, mixed international formats.', 'medium')
    if p.get('top') and p['distinct_count'] <= 30:
        return (f'Low-cardinality attribute; {p["distinct_count"]} distinct values.', 'medium')
    return (f'{c.replace("_", " ").capitalize()} attribute.', 'low')

DBDESC = {
 'MUSICSTORE_CORE': 'Music catalog system of record. Contains the artist and album masters, the central track catalog (with genre and media-type reference lists), and playlist definitions with their track assignments. Entity families: catalog content and its classification. No customer, sales, or financial data. Identifiers are dense integer surrogates starting at 1.',
 'MUSICSTORE_SALES': 'Sales and customer system of record (Teradata). Contains the customer master with contact details and assigned support representative, the employee master with reporting hierarchy, and invoicing (headers and line items). Line items reference track identifiers whose master data lives in the catalog SOR - no track attributes are stored here. No declared foreign keys; join intent is carried by Primary Index choices.',
}

# ---------- write bundles ----------
os.makedirs(OUT, exist_ok=True)
fingerprints = {}   # (db, table, col) -> set of hashes  (for step 2)
profiles = {}       # (db, table, col) -> profile
col_meta = {}       # (db, table, col) -> dict(indexed, unique, pi, pk, type, nullable)

for dbname, cfg in BUNDLES.items():
    schema = cfg['schema']
    dbdir = f'{OUT}/db/{dbname}'
    os.makedirs(f'{dbdir}/{schema}', exist_ok=True)
    os.makedirs(f'{dbdir}/fingerprints', exist_ok=True)
    index_lines = []
    for t in cfg['tables']:
        cols = columns_of(t)
        rc = DB.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        one_liner, conf, purpose = TABLE_DESC[t]
        flags = []
        if cfg['engine'] == 'teradata':
            flags.append(f'pi:{SALES_PI[t]}')
        fm = [
            '---', 'type: table', f'name: {schema}.{t}',
            f'description: {one_liner}', 'description_confirmed: false',
            f'database: {dbname}', f'engine: {cfg["engine"]}',
            f'row_count: {rc}', 'row_count_source: live',
            f'crawl_date: {TODAY}', f'flags: [{", ".join(flags)}]', '---', '',
            f'- [inferred:{conf}] Purpose: {purpose}', '', '## Columns', '',
        ]
        for c in cols:
            typ, nullable = col_types[(t, c)]
            p, distinct = profile(t, c)
            profiles[(dbname, t, c)] = p
            is_pk = cfg['keep_constraints'] and c in pks.get(t, [])
            fk = next(((rt, rc2) for (ft, _, fc, rt, rc2) in FKS if ft == t and fc == c), None) if cfg['keep_constraints'] else None
            idx = any(it == t and ic == c for (_, it, ic) in IDXS) if cfg.get('keep_indexes') else False
            is_pi = cfg['engine'] == 'teradata' and SALES_PI[t] == c
            col_meta[(dbname, t, c)] = {'indexed': idx or is_pk, 'pi': is_pi, 'pk': is_pk, 'type': typ}
            sens = (t, c) in SENSITIVE

            fm.append(f'### {c}')
            fm.append(f'- [observed] type: {typ}, {"nullable" if nullable else "not null"}')
            stat = f'- [observed] distinct_count: {p["distinct_count"]}; distinct_ratio: {p["distinct_ratio"]}; null_rate: {p["null_rate"]}'
            fm.append(stat)
            if 'len' in p:
                fm.append(f'- [observed] length: min {p["len"][0]}, max {p["len"][1]}, avg {p["len"][2]}')
            fm.append(f'- [observed] format: {p["format"]}; range: [{p["min"]!r} .. {p["max"]!r}]' if not sens
                      else f'- [observed] format: {p["format"]}  (sensitive-listed: range suppressed)')
            if p['dense_sequence']:
                fm.append('- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive')
            if is_pk: fm.append('- [observed] constraint: PRIMARY KEY')
            if fk: fm.append(f'- [observed] constraint: FOREIGN KEY -> {schema}.{fk[0]}.{fk[1]}')
            if idx and not is_pk: fm.append('- [observed] index: non-unique')
            if is_pi: fm.append('- [observed] index: PRIMARY INDEX (Teradata PI)')
            if sens:
                fm.append('- [observed] sensitive-listed: top-N and fingerprint suppressed')
                # specs/04 suppression vocabulary: absence of measurement, said out loud
                fm.append('- [observed] fingerprint: suppressed (sensitive)')
            else:
                if 'top' in p and p['distinct_count'] <= 30:
                    tv = ', '.join(f'{v}({round(100*f/p["non_null"])}%)' for v, f in p['top'][:6])
                    fm.append(f'- [observed] top_values: {tv}')
                eligible = p['distinct_ratio'] > FP_RATIO_GATE or idx or is_pk or is_pi
                if eligible and p['distinct_count'] > 0:
                    hashes, rules = fingerprint(distinct)
                    fingerprints[(dbname, t, c)] = set(hashes)
                    # schema segment required (specs/04): multi-schema databases
                    # collide without it
                    fp_path = f'fingerprints/{schema}.{t}.{c}.json'
                    json.dump({'algo': 'sha256/8B', 'normalization': rules,
                               'sample_cap': FP_SAMPLE, 'count': len(hashes),
                               'hashes': hashes}, open(f'{dbdir}/{fp_path}', 'w'))
                    fm.append(f'- [observed] fingerprint: sha256/8B @ {fp_path}')
                    fm.append(f'- [observed] normalization: [{", ".join(rules) if rules else "none"}]')
            d, dc = col_desc(t, c, p)
            fm.append(f'- [inferred:{dc}] {d}')
            fm.append('')
        open(f'{dbdir}/{schema}/{t}.md', 'w').write('\n'.join(fm))
        index_lines.append(f'- `{schema}/{t}.md` — {one_liner} ({rc} rows)')

    idx_md = ['---', 'type: index', f'database: {dbname}',
              f'description: {TABLE_DESC[cfg["tables"][0]][0].split(".")[0]}',  # placeholder, replaced below
              f'engine: {cfg["engine"]}', f'build_date: {TODAY}',
              'completeness: COMPLETE  # reconciliation: visible == cataloged',
              '---', '',
              f'- [inferred:high] {DBDESC[dbname]}', '',
              '## Tables', ''] + index_lines
    # fix description line
    idx_md[3] = f'description: {DBDESC[dbname].split(".")[0]}.'
    open(f'{dbdir}/index.md', 'w').write('\n'.join(idx_md))
    print(f'wrote bundle {dbname}: {len(cfg["tables"])} tables')

# ---------- step 2: relationship bundle (real computed Jaccard) ----------
def type_compat(t1, t2):
    num = lambda x: x.startswith(('INT', 'NUMERIC'))
    return (num(t1) and num(t2)) or (t1.startswith('VARCHAR') and t2.startswith('VARCHAR'))

pair_dir = f'{OUT}/rel/MUSICSTORE_CORE--MUSICSTORE_SALES'
os.makedirs(pair_dir, exist_ok=True)
edges_by_tablepair = {}
suppressed_dense = 0
low_evidence = 0
core_cols = [(t, c) for (d, t, c) in fingerprints if d == 'MUSICSTORE_CORE']
sales_cols = [(t, c) for (d, t, c) in fingerprints if d == 'MUSICSTORE_SALES']

for (ct, cc) in core_cols:
    for (st, sc) in sales_cols:
        if not type_compat(col_meta[('MUSICSTORE_CORE', ct, cc)]['type'],
                           col_meta[('MUSICSTORE_SALES', st, sc)]['type']):
            continue
        A = fingerprints[('MUSICSTORE_CORE', ct, cc)]
        B = fingerprints[('MUSICSTORE_SALES', st, sc)]
        if not A or not B: continue
        if min(len(A), len(B)) < 30:      # evidence floor
            low_evidence += 1
            continue
        inter = len(A & B)
        j = inter / len(A | B)
        cont = inter / min(len(A), len(B))
        ta = col_meta[('MUSICSTORE_CORE', ct, cc)]['type']
        tb = col_meta[('MUSICSTORE_SALES', st, sc)]['type']
        int_pair = ta.startswith(('INT','NUMERIC')) and tb.startswith(('INT','NUMERIC'))
        nsim0 = difflib.SequenceMatcher(None, cc, sc).ratio()
        if int_pair and cont >= 0.5 and nsim0 < 0.6:
            suppressed_dense += 1                # int surrogate domains: overlap non-distinctive
            continue
        if cont < 0.5: continue
        dense_pair = int_pair
        rc_a = profiles[('MUSICSTORE_CORE', ct, cc)]['total_rows']
        rc_b = profiles[('MUSICSTORE_SALES', st, sc)]['total_rows']
        boosts = []
        ma, mb = col_meta[('MUSICSTORE_CORE', ct, cc)], col_meta[('MUSICSTORE_SALES', st, sc)]
        if ma['indexed'] and mb['indexed']: boosts.append('idx-idx')
        if mb['pi']: boosts.append('pi-right')
        nsim = round(difflib.SequenceMatcher(None, cc, sc).ratio(), 2)
        boosts.append(f'name-sim:{nsim}')
        if cont >= 0.7:
            conf = 'high' if min(rc_a, rc_b) >= 1000 and not dense_pair else 'medium'
            status = 'candidate'
        else:
            conf, status = 'weak', 'weak'
        if int_pair: boosts.append('int-pair:name-gated')
        edges_by_tablepair.setdefault((ct, st), []).append(
            {'left': f'CORE.{ct}.{cc}', 'right': f'SALES.{st}.{sc}', 'jaccard': round(j, 3), 'containment': round(cont, 3),
             'rows': (rc_a, rc_b), 'boosts': boosts, 'confidence': conf, 'status': status})

pair_index = []
for (ct, st), edges in sorted(edges_by_tablepair.items()):
    lines = ['---', 'type: relationship',
             f'tables: [MUSICSTORE_CORE.CORE.{ct}, MUSICSTORE_SALES.SALES.{st}]',
             f'built_from: [db/MUSICSTORE_CORE@{TODAY}, db/MUSICSTORE_SALES@{TODAY}]',
             '---', '']
    for e in edges:
        lines.append(f'## {e["left"].split(".",1)[1]} <-> {e["right"].split(".",1)[1]}')
        lines.append(f'- [observed] containment: {e["containment"]} (primary); jaccard: {e["jaccard"]} (exact, hash-set)')
        lines.append(f'- [observed] evidence: {e["rows"][0]} x {e["rows"][1]} rows; confidence: {e["confidence"]}')
        lines.append(f'- [observed] normalization: left=[none], right=[none]')
        lines.append(f'- [observed] boosts: [{", ".join(e["boosts"])}]')
        if e['status'] == 'weak':
            lines.append('- status: weak    # Grade B visibility only')
        else:
            lines.append(f'- [inferred:{e["confidence"]}] Likely join: shared identifier population across SORs.')
        lines.append('')
    fname = f'{ct}--{st}.md'
    open(f'{pair_dir}/{fname}', 'w').write('\n'.join(lines))
    strong = sum(1 for e in edges if e['status'] != 'weak')
    pair_index.append(f'- `{fname}` — {strong} candidate, {len(edges)-strong} weak edge(s)')
    for e in edges:
        print(f"  edge: {e['left']} <-> {e['right']}  C={e['containment']} J={e['jaccard']} {e['status']} {e['boosts']}")

open(f'{pair_dir}/index.md', 'w').write('\n'.join(
    ['---', 'type: index', 'databases: [MUSICSTORE_CORE, MUSICSTORE_SALES]',
     'description: Cross-SOR edges between music catalog and sales databases.',
     f'build_date: {TODAY}', 'completeness: COMPLETE',
     f'suppressed_int_pairs: {suppressed_dense}', f'below_evidence_floor: {low_evidence}', '---', '',
     '- [inferred:high] Sales line items reference catalog track identifiers; '
     'edges below were scored offline from step 1 fingerprints (exact Jaccard on hash sets).',
     '', '## Table pairs', ''] + pair_index))
print('relationship bundle written')
