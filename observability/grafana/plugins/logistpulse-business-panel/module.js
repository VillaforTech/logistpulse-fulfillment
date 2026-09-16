System.register(['react','@grafana/data'],function(exports){
 let React,PanelPlugin;
 return {setters:[m=>{React=m.default||m},m=>{PanelPlugin=m.PanelPlugin}],execute:function(){
  function BusinessPanel(props){
   const [now,setNow]=React.useState(Date.now());
   const latest=React.useRef({revision:-1});const arrived=React.useRef(0);
   React.useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),100);return()=>clearInterval(timer)},[]);
   for(const frame of props.data.series||[]){
    const row={};
    for(const field of frame.fields){const values=field.values;row[field.name]=values.length?(values.get?values.get(values.length-1):values[values.length-1]):null}
    if(Number(row.revision)>Number(latest.current.revision)){latest.current=row;arrived.current=Date.now()}
   }
   const row=latest.current;const calculated=Date.parse(row.computed_at);
   const stale=!Number.isFinite(calculated)||now-calculated>3000||now-arrived.current>3000;
   const quality=stale?'STALE':Number(row.quality)===2?'INCOMPLETE':Number(row.quality)===0?'FRESH':'STALE';
   const field=props.options.field||'lk1';const number=Number(row[field]);const noSample=number===-2;
   const value=quality!=='FRESH'?'DATOS NO CONFIABLES':noSample?'SIN MUESTRA':row[field]==null?'SIN DATO':number.toLocaleString('es-EC',{maximumFractionDigits:2});
   const color=quality!=='FRESH'||noSample?'#c3c8ce':number>0?'#ff7373':'#73d9a6';
   return React.createElement('section',{
    style:{padding:16,height:'100%',overflow:'auto'},
    'data-logistpulse-panel':field,'data-revision':String(row.revision),'data-event-id':row.source_event_id||'',
    'data-correlation-id':row.correlation_id||'','data-aggregate-id':row.aggregate_id||'',
    'data-aggregate-version':String(row.aggregate_version||0),'data-quality':quality,
    'data-value':String(row[field]),'data-sample':String(row.sample??0),'data-computed-at':row.computed_at||''
   },React.createElement('div',{'data-logistpulse-value':true,style:{fontSize:36,fontWeight:650,color}},value),
    React.createElement('div',{style:{fontSize:13,color}},quality+(quality==='FRESH'&&number>0?' · ALERTA':'')),
    React.createElement('div',{style:{fontSize:11,marginTop:10}},`Muestra L-K1: ${row.sample??'—'} · revisión ${row.revision??'—'}`),
    React.createElement('div',{style:{fontSize:10,overflowWrap:'anywhere'}},`Pedido ${row.aggregate_id||'sin eventos'} · v${row.aggregate_version||0}`),
    React.createElement('div',{style:{fontSize:10,overflowWrap:'anywhere'}},row.correlation_id||''),
    React.createElement('div',{style:{fontSize:10}},row.computed_at||'Esperando snapshot'));
  }
  exports('plugin',new PanelPlugin(BusinessPanel).setPanelOptions(b=>b.addTextInput({path:'field',name:'Campo de KPI',defaultValue:'lk1'})));
 }};
});
