"""Optional real Mesa-agent and hidden GLFW context checks in isolated storage."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from launcher.core import SSL_CONTEXT, USER_AGENT, Task
from launcher.launch_options import java_architecture, prepare_renderer, renderer_arguments
from launcher.service import Application

PROBE='''import org.lwjgl.glfw.GLFW;
import org.lwjgl.opengl.GL;
import org.lwjgl.opengl.GL11;
public class RendererProbe {
 public static void main(String[] args) {
  org.lwjgl.glfw.GLFWErrorCallback.createPrint(System.err).set();
  if (!GLFW.glfwInit()) throw new IllegalStateException("GLFW init failed");
  GLFW.glfwWindowHint(GLFW.GLFW_VISIBLE, GLFW.GLFW_FALSE);
  GLFW.glfwWindowHint(GLFW.GLFW_CONTEXT_VERSION_MAJOR,3);
  GLFW.glfwWindowHint(GLFW.GLFW_CONTEXT_VERSION_MINOR,3);
  long window=GLFW.glfwCreateWindow(64,64,"StarMoon renderer probe",0,0);
  if(window==0) throw new IllegalStateException("Context creation failed");
  GLFW.glfwMakeContextCurrent(window);
  GL.createCapabilities();
  System.out.println("RENDERER="+GL11.glGetString(GL11.GL_RENDERER));
  System.out.println("GL_VERSION="+GL11.glGetString(GL11.GL_VERSION));
  GLFW.glfwDestroyWindow(window);
  GLFW.glfwTerminate();
 }
}
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--probe-graphics",action="store_true")
    args=parser.parse_args()
    output=ROOT/"test-results/renderer-integration"
    output.mkdir(parents=True,exist_ok=True)
    results={}
    with tempfile.TemporaryDirectory(prefix="renderer-check-",dir=str(output)) as directory:
        app=Application(Path(directory)/"data")
        candidates=[item for item in app.game.java_candidates() if item["major"]>=17]
        assert candidates,"Java 17+ is required for this isolated probe"
        java=next((item["path"] for item in candidates if item["major"]==17),candidates[0]["path"])
        task=Task("Renderer integration",None)
        classpath=[]
        if args.probe_graphics:
            arch=java_architecture(java)
            native="natives-windows"+({"x64":"","x86":"-x86","arm64":"-arm64"}[arch])
            for artifact in ("lwjgl","lwjgl-glfw","lwjgl-opengl"):
                for classifier in ("",native):
                    filename=artifact+"-3.3.3"+("-"+classifier if classifier else "")+".jar"
                    url="https://repo.maven.apache.org/maven2/org/lwjgl/"+artifact+"/3.3.3/"+filename
                    request=urllib.request.Request(url+".sha1",headers={"User-Agent":USER_AGENT})
                    with urllib.request.urlopen(request,timeout=25,context=SSL_CONTEXT) as response:
                        digest=response.read(1024).decode().strip().split()[0]
                    assert re.fullmatch(r"[a-fA-F0-9]{40}",digest)
                    target=Path(directory)/filename
                    app.net.download(url,target,sha1=digest,task=task)
                    classpath.append(str(target))
            source=Path(directory)/"RendererProbe.java"
            source.write_text(PROBE,encoding="utf-8")
        for driver in ("llvmpipe","zink","d3d12"):
            instance={"renderer":driver}
            prepare_renderer(app.game,instance,java,task)
            runtime=Path(directory)/driver
            runtime.mkdir()
            command=[java]+renderer_arguments(app.store,instance,java,runtime)
            command += ["--class-path",os.pathsep.join(classpath),str(source)] if args.probe_graphics else ["-version"]
            result=subprocess.run(command,cwd=directory,capture_output=True,timeout=60,
                                  creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            text=(result.stdout+result.stderr).decode("utf-8",errors="replace")
            (output/(driver+".log")).write_text(text,encoding="utf-8")
            renderer=next((line.split("=",1)[1] for line in text.splitlines() if line.startswith("RENDERER=")),"")
            results[driver]={"returncode":result.returncode,"renderer":renderer,"native_files":len(list(runtime.rglob("*.dll")))}
            print(driver,json.dumps(results[driver]),flush=True)
        (output/"results.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
        assert all(item["returncode"]==0 for item in results.values()),"See individual renderer logs"
        if args.probe_graphics:
            assert all(driver in item["renderer"].lower() for driver,item in results.items()),"Mesa renderer was not selected"


if __name__=="__main__":
    main()
