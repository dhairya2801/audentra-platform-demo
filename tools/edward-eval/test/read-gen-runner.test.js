import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {spawn} from "node:child_process";
import {mkdirSync, mkdtempSync, readFileSync, writeFileSync, rmSync} from "node:fs";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

test("expired question templates produce a reported skip without sending a model request", async () => {
  const root=fileURLToPath(new URL("../../../",import.meta.url));
  mkdirSync(join(root,"artifacts"),{recursive:true});
  const scratch=mkdtempSync(join(root,"artifacts/read-gen-runner-"));
  const batch="runner-skip-"+process.pid;
  const output=join(root,"artifacts/runs",batch);
  const truth=join(scratch,"truth.json");
  writeFileSync(truth,JSON.stringify({staff:{byRef:{"SYN-STF-ADM-C08":{id:"test-staff",name:"Zelda",appointments:{next14List:[]}}}}}));
  const paths=[];
  const server=createServer((request,response)=>{
    paths.push(request.url);response.writeHead(request.url==="/health"?200:500);response.end("{}");
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  try {
    const result=await new Promise((resolve,reject)=>{
      const child=spawn(process.execPath,["tools/edward-eval/read-gen/run.mjs","--ids","rg-apt-007","--batch",batch],{
        cwd:root,env:{...process.env,READ_GEN_BASE_URL:`http://127.0.0.1:${server.address().port}`,READ_GEN_GROUND_TRUTH:truth},
      });
      let log="";child.stdout.on("data",x=>log+=x);child.stderr.on("data",x=>log+=x);child.on("error",reject);child.on("close",code=>resolve({code,log}));
    });
    assert.equal(result.code,0,result.log);
    assert.deepEqual(paths,["/health"]);
    const summary=JSON.parse(readFileSync(join(output,"summary.json")));
    assert.equal(summary.SKIP,1);assert.equal(summary.PASS,0);assert.equal(summary.FAIL,0);
    const transcript=JSON.parse(readFileSync(join(output,"transcript.json")));
    assert.equal(transcript[0].turns[0].failures[0].kind,"ground_truth_unavailable");
    assert.match(transcript[0].turns[0].question,/\{\{gt:/);
  } finally {
    await new Promise(resolve=>server.close(resolve));
    rmSync(scratch,{recursive:true,force:true});rmSync(output,{recursive:true,force:true});
  }
});
