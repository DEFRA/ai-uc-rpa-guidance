---
title: Charts
description: Add bar charts to GOV.UK content with Govspeak Markdown code.
---

Text formatting

# Charts

You can add ‘simple’, ‘stacked’ and ‘compact’ bar charts to your content. You can combine these different types within one bar chart.

All bar charts can display negative values.

Users can switch between a bar chart and a table view on the webpage.

Note: 

See [examples of different bar charts on GOV.UK](https://www.gov.uk/government/publications/examples-of-visual-content-to-use-on-govuk/examples-of-visual-content-to-use-on-govuk).

To add a chart, make a numeric table using the [tables code](/formatting-content/text-formatting/tables/). You then need to add a line of code below the table, which is different depending on the type of bar chart you want.

## Simple bar chart

Add `{barchart}` below the table.

If there is more than 1 column of numbers for each row, they will display as grouped bars.

For example:

```
Department | Short speeches | Long speeches
-|-|-
Department 1 | 6 | 6
Department 2 | 6 | 8
Department 3 | 18 | 2
{barchart}
```

## Stacked bar chart

Add `{barchart stacked}` below the table.

The first column must include the name of each category. The final column must include the total.

For example:

```
Department | Short Speeches | Long Speeches | Total
-|-|-|-
Department 1 | 6 | 6 | 12
Department 2 | 6 | 8 | 14
Department 3 | 18 | 2 | 20
{barchart stacked}
```

## Compact bar chart

Add `{barchart compact}` below the table to save space by shrinking the chart.

For example:

```
>Department | Short Speeches | Long Speeches
-|-|-
Department 1 | 6 | 6
Department 2 | 6 | 8
Department 3 | 18 | 2
Department 4 | 5 | 4
Department 5 | 7 | 7
Department 6 | 11 | 1
{barchart compact}
```

## Negative bar chart

If you have negative data values, add `{barchart negative}` at the end of the table.

For example:

```
Department | Change in number of buildings
-|-
Department 1 | -12
Department 2 | 3
Department 3 | -4
Department 4 | 2
{barchart negative}
```

## Combine different bar chart styles

Combine the different styles of bar charts by using more than one tag on separate lines. For example, to add a compact negative chart, use both:

`{barchart negative}`  
`{barchart compact}`

## Colours in bar charts

You can use up to 6 colours to represent different categories when you add a bar chart on GOV.UK. The colours and the order they appear cannot be changed.

This is so they’re accessible for users who are colour blind or have low vision.

## Add other types of charts

If you want to add another type of chart, like a line graph or pie chart, you’ll need to upload it as an image. Read the guidance on [formatting images](/formatting-content/images/) to find out how to create an accessible image.

## Share this page

The following links open in a new tab

* [Share by
  Email
  (opens in new tab)](mailto:?body=https://guidance.publishing.service.gov.uk/formatting-content/text-formatting/charts/)
* [Share on
  Microsoft Teams
  (opens in new tab)](https://teams.microsoft.com/share?href=https://guidance.publishing.service.gov.uk/formatting-content/text-formatting/charts/)