## R53 Traffic Shape CLI

Python CLI interface using [rich](https://github.com/willmcgugan/rich) for quickly viewing R53 records, updating Route53 weighted record values, and inverting latency record healthchecks.

While R53 should be configured using Terraform or other IaC tools, it is useful to have a quick CLI to be able to view and update traffic patterns via R53 from a local machine.

![menu](https://github.com/rollerd/r53_traffic_shape_cli/blob/master/imgs/menu.png)

![changes](https://github.com/rollerd/r53_traffic_shape_cli/blob/master/imgs/changes.png)

![recordlist](https://github.com/rollerd/r53_traffic_shape_cli/blob/master/imgs/record_list.png)

#### Load Changesets From File
You can create a json file that contains the fields for the updated records that you would like to apply.
Useful for large changes that get made regularly


#### Status
Script is cluttered and messy right now, but working for updating weighted record values, viewing all records, viewing weighted records, viewing latency records.

This was mostly a project to play around with [rich](https://github.com/willmcgugan/rich), which is a lot of fun and really nice to use.


#### TODO
- Add latency record healthcheck inversion support
- Add tests
- Create traffic live screen (plugin API?)
