import type { Call } from './types';
const enc = new TextEncoder();
const xml = (v:unknown) => String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&apos;');
const crcTable = Array.from({length:256},(_,i)=>{let c=i;for(let j=0;j<8;j++)c=c&1?0xedb88320^(c>>>1):c>>>1;return c>>>0});
const crc32 = (bytes:Uint8Array) => {let c=0xffffffff;for(const b of bytes)c=crcTable[(c^b)&255]^(c>>>8);return (c^0xffffffff)>>>0};
const u16=(n:number)=>[n&255,(n>>>8)&255];
const u32=(n:number)=>[n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255];
function col(n:number){let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s}
function zip(files:{name:string;body:string}[]):Blob{
  const parts:Uint8Array[]=[];const central:Uint8Array[]=[];let offset=0;
  for(const file of files){const name=enc.encode(file.name),data=enc.encode(file.body),crc=crc32(data);const local=new Uint8Array([...u32(0x04034b50),...u16(20),...u16(0),...u16(0),...u16(0),...u16(0),...u32(crc),...u32(data.length),...u32(data.length),...u16(name.length),...u16(0),...name]);parts.push(local,data);const head=new Uint8Array([...u32(0x02014b50),...u16(20),...u16(20),...u16(0),...u16(0),...u16(0),...u16(0),...u32(crc),...u32(data.length),...u32(data.length),...u16(name.length),...u16(0),...u16(0),...u16(0),...u16(0),...u32(0),...u32(offset),...name]);central.push(head);offset+=local.length+data.length}
  const centralSize=central.reduce((n,x)=>n+x.length,0);const end=new Uint8Array([...u32(0x06054b50),...u16(0),...u16(0),...u16(files.length),...u16(files.length),...u32(centralSize),...u32(offset),...u16(0)]);
  const all=[...parts,...central,end]; const buffer=new ArrayBuffer(all.reduce((n,p)=>n+p.length,0)); const bytes=new Uint8Array(buffer); let at=0; for(const part of all){bytes.set(part,at);at+=part.length} return new Blob([buffer],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}
export function exportCallsXlsx(calls:Call[]){
  const rows=[['Time','Direction','Number','Channel','Mode','Duration','Outcome','Recording'],...calls.map(c=>[c.startedAt,c.direction,c.phoneNumber,c.channelType,c.mode,c.durationSeconds,c.outcome,c.recordingState])];
  const sheet='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+rows.map((row,r)=>'<row r="'+(r+1)+'">'+row.map((value,c)=>{const ref=col(c)+(r+1);return typeof value==='number'?'<c r="'+ref+'"><v>'+value+'</v></c>':'<c r="'+ref+'" t="inlineStr"><is><t>'+xml(value)+'</t></is></c>'}).join('')+'</row>').join('')+'</sheetData></worksheet>';
  const files=[
    {name:'[Content_Types].xml',body:'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'},
    {name:'_rels/.rels',body:'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'},
    {name:'xl/workbook.xml',body:'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Calls" sheetId="1" r:id="rId1"/></sheets></workbook>'},
    {name:'xl/_rels/workbook.xml.rels',body:'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'},
    {name:'xl/worksheets/sheet1.xml',body:sheet}
  ];
  const url=URL.createObjectURL(zip(files));const a=document.createElement('a');a.href=url;a.download='voxops-calls.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}


