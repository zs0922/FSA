ThisBuild / scalaVersion := "2.13.16"

val chisel6Version = "6.7.0"

lazy val chisel6Settings = Seq(
  libraryDependencies ++= Seq("org.chipsalliance" %% "chisel" % chisel6Version),
  addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % chisel6Version cross CrossVersion.full)
)

lazy val root = (project in file("."))
  .settings(
    name := "easyfloat"
  ).settings(chisel6Settings)