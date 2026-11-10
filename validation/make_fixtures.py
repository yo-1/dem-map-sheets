from pathlib import Path
import numpy as np,json,csv
root=Path(__file__).resolve().parent/'data'
for case in ['year','accuracy','density','coverage','first','last','unknown','json']:
    folder=root/case;folder.mkdir(parents=True,exist_ok=True)
    a=np.full((15,40),100.);a[:,20:]=200.
    b=np.full((15,40),300.);b[:,20:]=400.
    a[0,0]=-999999;b[0,39]=-999999
    if case=='coverage':
        a[:,0:10]=-999999;b[:,30:40]=-999999
        b[0,39]=-999999
    for name,array in [('a',a),('b',b)]:
        with (folder/(name+'.asc')).open('w') as f:
            f.write('ncols 40\nnrows 15\nxllcorner 0\nyllcorner -1500\ncellsize 100\nNODATA_value -999999\n')
            np.savetxt(f,array,fmt='%.1f')
    ra=dict(source='a.asc',survey_year=2024,accuracy_m=.2,ground_density=4)
    rb=dict(source='b.asc',survey_year=2024,accuracy_m=.2,ground_density=4)
    if case in ('year','json'):rb.update(survey_year=2025,accuracy_m=9,ground_density=.1)
    if case=='accuracy':rb.update(accuracy_m=.1,ground_density=.1)
    if case=='density':rb.update(ground_density=5)
    if case=='unknown':rb.update(survey_year=None,accuracy_m=.01,ground_density=100)
    with (folder/'metadata.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(ra));w.writeheader();w.writerows([ra,rb])
    (folder/'metadata.json').write_text(json.dumps({'sources':[ra,rb]},indent=2),encoding='utf-8')
print(root)
