import pandas as pd,numpy as np,re,unicodedata,sys
ext,out=sys.argv[1],sys.argv[2]
def norm(n):
    n=unicodedata.normalize('NFKD',str(n)); n=''.join(c for c in n if not unicodedata.combining(c))
    n=re.sub(r"[.'’\-]","",n.lower()); n=re.sub(r"\s+(jr|sr|ii|iii|iv|v)$","",n.strip()); return re.sub(r"\s+"," ",n)
cols=['game_id','play_id','season_type','week','posteam','defteam','home_team','away_team','yardline_100','play_type','two_point_attempt','rusher_player_id','receiver_player_id','touchdown','td_team','td_player_id','td_player_name','total_line','spread_line','game_seconds_remaining','desc']
rows=[]
for season,suf in ((2025,'25'),(2026,'')):
    p=pd.read_csv(f'{ext}/pbp{suf}.csv.gz',usecols=cols,low_memory=False); p=p[p.season_type=='REG'].copy(); 
    st=pd.read_csv(f'{ext}/stats{suf}.csv',low_memory=False); st=st[st.season_type=='REG']
    sn=pd.read_csv(f'{ext}/snaps{suf}.csv',low_memory=False); sn=sn[sn.game_type=='REG']
    idpos=st.groupby('player_id').position.last().to_dict(); idname=st.groupby('player_id').player_display_name.last().to_dict()
    # snap key
    sn['k']=sn.player.map(norm); snap=sn.set_index(['team','k','week']).offense_pct.to_dict()
    teams_wk=sn.groupby('team').week.apply(lambda x:sorted(set(x))).to_dict()
    # games
    g=p.groupby('game_id').agg(week=('week','first'),home=('home_team','first'),away=('away_team','first'),total=('total_line','first'),spread=('spread_line','first')).reset_index()
    # usage
    pl=p[(p.two_point_attempt!=1)&p.play_type.isin(['run','pass'])].copy()
    pl['pid']=np.where(pl.play_type=='run',pl.rusher_player_id,pl.receiver_player_id); pl=pl[pl.pid.notna()]
    pl['rz']=(pl.yardline_100<=20).astype(int); pl['i10']=(pl.yardline_100<=10).astype(int); pl['opp']=1
    u=pl.groupby(['week','posteam','pid'])[['opp','rz','i10']].sum().reset_index().rename(columns={'posteam':'team'})
    # tds
    td=p[(p.touchdown==1)&p.td_player_id.notna()].sort_values(['game_id','play_id'])
    tdc=td.groupby(['week','td_team','td_player_id']).size().rename('tds').reset_index().rename(columns={'td_team':'team','td_player_id':'pid'})
    tfirst=td.groupby(['game_id','td_team'],as_index=False).first()[['week','td_team','td_player_id']].rename(columns={'td_team':'team','td_player_id':'pid'}); tfirst['tf']=1
    gfirst=td.groupby('game_id',as_index=False).first()[['week','td_team','td_player_id']].rename(columns={'td_team':'team','td_player_id':'pid'}); gfirst['gf']=1
    # roster universe: stats rows per week + usage
    base=st[st.position.isin(['RB','WR','TE','QB'])][['week','team','player_id','position','player_display_name']].rename(columns={'player_id':'pid','position':'pos','player_display_name':'name'})
    d=base.merge(u,how='left',on=['week','team','pid']).merge(tdc,how='left',on=['week','team','pid']).merge(tfirst,how='left',on=['week','team','pid']).merge(gfirst,how='left',on=['week','team','pid']).fillna({'opp':0,'rz':0,'i10':0,'tds':0,'tf':0,'gf':0})
    d['season']=season; d['k']=d.name.map(norm)
    d['snap']=[snap.get((t,k,w),np.nan) for t,k,w in zip(d.team,d.k,d.week)]
    # game context
    ctx=[]
    for _,r in g.iterrows():
        h=(r.total+r.spread)/2;a=(r.total-r.spread)/2
        ctx+= [dict(week=r.week,team=r.home,opp_team=r.away,home=1,imp=h,oimp=a,game_id=r.game_id),dict(week=r.week,team=r.away,opp_team=r.home,home=0,imp=a,oimp=h,game_id=r.game_id)]
    ctx=pd.DataFrame(ctx); d=d.merge(ctx,on=['week','team'],how='inner')
    # team totals per week
    tw=d.groupby(['team','week'])[['opp','rz','i10','tds']].sum().add_prefix('t_').reset_index()
    d=d.merge(tw,on=['team','week'])
    d=d.sort_values(['team','pid','week'])
    F=[]
    for (t,pid),x in d.groupby(['team','pid']):
        x=x.sort_values('week')
        for i,(idx,r) in enumerate(x.iterrows()):
            pr=x.iloc[:i]
            if len(pr)<2: continue
            l3=pr.tail(3)
            gp=len(pr)
            F.append(dict(idx=idx,gp=gp,rzg=pr.rz.mean(),i10g=pr.i10.mean(),oppg=pr.opp.mean(),tdg=pr.tds.mean(),tfg=pr.tf.mean(),
              rz3=l3.rz.mean(),i103=l3.i10.mean(),opp3=l3.opp.mean(),td3=l3.tds.mean(),
              rzsh=pr.rz.sum()/max(pr.t_rz.sum(),1),i10sh=pr.i10.sum()/max(pr.t_i10.sum(),1),oppsh=pr.opp.sum()/max(pr.t_opp.sum(),1),
              rzsh3=l3.rz.sum()/max(l3.t_rz.sum(),1),i10sh3=l3.i10.sum()/max(l3.t_i10.sum(),1),
              snaplast=pr.snap.iloc[-1],snap3=l3.snap.mean(),rztot=pr.rz.sum(),tftot=pr.tf.sum(),tdtot=pr.tds.sum()))
    F=pd.DataFrame(F).set_index('idx'); d=d.join(F,how='inner')
    # team offense & defense context prior
    off=d.groupby(['team','week'])[['t_rz','t_i10','t_tds']].first().reset_index().sort_values('week')
    for c in ['t_rz','t_i10','t_tds']:
        off['pr_'+c]=off.groupby('team')[c].transform(lambda s:s.shift().expanding().mean())
    d=d.merge(off[['team','week','pr_t_rz','pr_t_i10','pr_t_tds']],on=['team','week'])
    # defense allowed by position (prior per game)
    all_=base.merge(tdc,on=['week','team','pid']).merge(ctx[['week','team','opp_team']],on=['week','team'])
    al=all_.groupby(['opp_team','week','pos']).tds.sum().rename('al').reset_index()
    wk=ctx[['opp_team','week']].rename(columns={'opp_team':'d'}).drop_duplicates()
    rec=[]
    for pos in ['RB','WR','TE','QB']:
        for dt,w in wk.values:
            pr=al[(al.opp_team==dt)&(al.pos==pos)&(al.week<w)].al.sum(); ng=((wk.d==dt)&(wk.week<w)).sum()
            rec.append(dict(opp_team=dt,week=w,pos=pos,dpos=pr/ng if ng else np.nan,dgp=ng))
    d=d.merge(pd.DataFrame(rec),on=['opp_team','week','pos'])
    tf_def=td.groupby(['game_id','td_team'],as_index=False).first()
    rows.append(d)
D=pd.concat(rows); D['any']=(D.tds>0).astype(int)
D['played']=D.snap.fillna(0)>0
D.to_pickle(out); print(D.groupby('season').agg(n=('any','size'),played=('played','sum'),any=('any','mean'),maxw=('week','max'),games=('game_id','nunique')))
print(D[D.season==2026].groupby('week').game_id.nunique().to_dict())
