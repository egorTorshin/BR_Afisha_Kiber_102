"""Small Prometheus text exporter with bounded, PII-free labels."""
from collections import defaultdict
from dataclasses import dataclass
from threading import Lock

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


@dataclass
class Sample:
    name: str
    value: float
    labels: dict


@dataclass
class Family:
    name: str
    samples: list[Sample]


class Registry:
    def __init__(self): self.metrics = []
    def register(self, metric): self.metrics.append(metric)
    def collect(self): return [m.family() for m in self.metrics]


class Metric:
    def __init__(self, name, doc, labelnames=(), registry=None):
        self.name, self.doc, self.labelnames = "afisha_" + name, doc, tuple(labelnames)
        self.values = defaultdict(float)
        self.lock = Lock()
        (registry or REGISTRY).register(self)
    def labels(self, *values, **kwargs):
        labels = dict(zip(self.labelnames, values)) if values else kwargs
        if set(labels) != set(self.labelnames): raise ValueError("Invalid metric labels")
        return BoundMetric(self, tuple(str(labels[k]) for k in self.labelnames))
    def inc(self, amount=1):
        if self.labelnames: raise ValueError("labels required")
        self.labels().inc(amount)
    def set(self, amount):
        if self.labelnames: raise ValueError("labels required")
        self.labels().set(amount)
    def observe(self, amount):
        if self.labelnames: raise ValueError("labels required")
        self.labels().observe(amount)
    def family(self):
        with self.lock: samples = [Sample(self.name + ("_total" if isinstance(self, Counter) else ""), value, dict(zip(self.labelnames, labels))) for labels, value in self.values.items()]
        return Family(self.name.removeprefix("afisha_"), samples)


class BoundMetric:
    def __init__(self, metric, labels): self.metric, self.labels = metric, labels
    def inc(self, amount=1):
        with self.metric.lock: self.metric.values[self.labels] += amount
    def set(self, amount):
        with self.metric.lock: self.metric.values[self.labels] = amount
    def observe(self, amount):
        with self.metric.lock:
            if isinstance(self.metric, Histogram): self.metric.values[self.labels].append(amount)
            else: self.metric.values[self.labels] += amount


class Counter(Metric): pass
class Gauge(Metric): pass
class Histogram(Metric):
    def observe(self, amount):
        if self.labelnames: raise ValueError("labels required")
        self.labels().observe(amount)
    def __init__(self,name,doc,labelnames=(),buckets=(),registry=None):
        self.buckets=tuple(buckets); super().__init__(name,doc,labelnames,registry)
        self.values=defaultdict(list)
    def family(self):
        samples=[]
        with self.lock: rows=list(self.values.items())
        for labels, observations in rows:
            ld=dict(zip(self.labelnames,labels))
            for bound in self.buckets: samples.append(Sample(self.name+"_bucket",sum(x<=bound for x in observations),{**ld,"le":str(bound)}))
            samples.append(Sample(self.name+"_bucket",len(observations),{**ld,"le":"+Inf"}))
            samples.extend((Sample(self.name+"_count",len(observations),ld),Sample(self.name+"_sum",sum(observations),ld)))
        return Family(self.name.removeprefix("afisha_"),samples)


REGISTRY=Registry()
HTTP_REQUESTS=Counter("http_requests", "HTTP requests", ["channel","method","status"])
HTTP_LATENCY=Histogram("http_request_duration_seconds", "HTTP request duration", ["channel","method"],(.005,.01,.025,.05,.1,.25,.5,1,2.5,5,10))
BOT_REQUESTS=Counter("bot_updates", "Telegram updates processed", ["channel","status"])
BOT_LATENCY=Histogram("bot_update_duration_seconds", "Telegram update processing duration", buckets=(.01,.05,.1,.25,.5,1,2.5,5,10,30))
AI_CALLS=Counter("ai_calls", "AI route and model decisions", ["component","outcome"])
AI_LATENCY=Histogram("ai_duration_seconds", "AI component duration", ["component"],(.005,.01,.025,.05,.1,.25,.5,1,2.5,5,10,30,60,120))
AI_TOKENS=Counter("ai_tokens", "AI tokens used", ["model","kind"])
AI_COST=Counter("ai_cost_usd", "AI known cost in USD")
AI_FALLBACKS=Counter("ai_fallbacks", "AI fallbacks", ["reason"])
APP_UP=Gauge("app_up", "API process is available")
APP_UP.labels().set(1)
DATABASE_UP=Gauge("database_up", "Configured database responds to ping", ["database"])
DATABASE_LATENCY=Histogram("database_ping_duration_seconds", "Database ping duration", ["database"],(.001,.005,.01,.025,.05,.1,.25,.5,1,2.5,5))

def escape(value): return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
def generate_latest(registry=REGISTRY):
    lines=[]
    for family in registry.collect():
        metric=next(m for m in registry.metrics if m.name.removeprefix("afisha_")==family.name)
        kind="histogram" if isinstance(metric,Histogram) else "counter" if isinstance(metric,Counter) else "gauge"
        lines.extend((f"# HELP {metric.name} {metric.doc}",f"# TYPE {metric.name} {kind}"))
        for sample in family.samples:
            labels=",".join(f'{k}="{escape(v)}"' for k,v in sample.labels.items())
            lines.append(f"{sample.name}{{{labels}}} {sample.value}" if labels else f"{sample.name} {sample.value}")
    return ("\n".join(lines)+"\n").encode()
def render_metrics():
    from fastapi.responses import Response
    return Response(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)
